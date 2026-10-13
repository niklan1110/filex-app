// Job progress, result and failure (design.md "Progress", "Result", "Errors"). Polls
// GET /jobs/{id} with If-None-Match about once a second, slower while the tab is hidden or the
// server does not answer. Every string from the job (names, messages) is rendered as text.
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ApiError, deleteJob, pollJob, type Job, type JobOutput } from "../api/client";
import { errorTitle, expiresIn, formatBytes, warningText } from "../messages";
import { Alert } from "./Alert";
import { BeforeAfter, PREVIEW_TOOLS } from "./BeforeAfter";
import { DownloadButton } from "./DownloadButton";
import { Icon } from "./Icon";
import { OutcomeBlock, TargetBadge, outcomeOf } from "./Outcome";

const TERMINAL = new Set<Job["status"]>(["SUCCEEDED", "FAILED", "CANCELLED"]);
const VISIBLE_MS = 1000;
const HIDDEN_MAX_MS = 30_000;
const ERROR_MAX_MS = 15_000;

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e));
}

/** Poll until the job is terminal; returns the latest job and the last polling error. */
function useJob(initial: Job, stopped: boolean) {
  const [job, setJob] = useState(initial);
  const [error, setError] = useState<{ err: ApiError; failures: number } | null>(null);
  const done = TERMINAL.has(job.status) || stopped;
  const id = job.id;

  useEffect(() => {
    if (done) return;
    const ac = new AbortController();
    let etag: string | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let hiddenMs = 2 * VISIBLE_MS;
    let inFlight = false;

    const schedule = () => {
      const hidden = document.visibilityState === "hidden";
      let ms = VISIBLE_MS;
      if (failures) ms = Math.min(ERROR_MAX_MS, VISIBLE_MS * 2 ** failures);
      else if (hidden) {
        ms = hiddenMs;
        hiddenMs = Math.min(HIDDEN_MAX_MS, hiddenMs * 2);
      }
      timer = setTimeout(tick, ms);
    };
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      clearTimeout(timer);
      try {
        const r = await pollJob(id, etag, ac.signal);
        etag = r.etag;
        failures = 0;
        setError(null);
        if (r.job) {
          setJob(r.job);
          if (TERMINAL.has(r.job.status)) return;
        }
      } catch (e) {
        if (ac.signal.aborted) return;
        const err = asApiError(e);
        failures++;
        setError({ err, failures });
        if (err.status === 404) return; // expired or deleted elsewhere: stop
      } finally {
        inFlight = false;
      }
      if (!ac.signal.aborted) schedule();
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      hiddenMs = 2 * VISIBLE_MS;
      void tick();
    };
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      ac.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [id, done]);

  return { job, error };
}

export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

const BADGES: Record<Job["status"], { cls: string; icon: "clock" | "check" | "x" | null; text: string }> = {
  QUEUED: { cls: "badge-neutral", icon: "clock", text: "Queued" },
  RUNNING: { cls: "badge-ink", icon: null, text: "Running" },
  SUCCEEDED: { cls: "badge-ok", icon: "check", text: "Done" },
  FAILED: { cls: "badge-danger", icon: "x", text: "Failed" },
  CANCELLED: { cls: "badge-neutral", icon: "x", text: "Cancelled" },
};

export function StatusBadge({ status }: { status: Job["status"] }) {
  const badge = BADGES[status];
  return (
    <span className={`badge ${badge.cls}`}>
      {badge.icon ? <Icon name={badge.icon} size={14} /> : <span className="spinner spinner-sm" aria-hidden="true" />}
      {badge.text}
    </span>
  );
}

type Props = {
  initial: Job;
  toolTitle: string;
  onStartOver: () => void;
  /** The files the user picked, in upload order (for the before/after preview). */
  files?: File[];
  /** The options that were sent (for the target-size verdict). */
  options?: Record<string, unknown>;
  /** Words for each step by index ("DOCX to PDF (Office to PDF)"); the step's tool id otherwise. */
  stepLabels?: string[];
};

export function JobView({ initial, toolTitle, onStartOver, files = [], options = {}, stepLabels = [] }: Props) {
  const [deleted, setDeleted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const { job, error } = useJob(initial, deleted);
  const now = useNow(60_000);
  const terminal = TERMINAL.has(job.status);
  const headRef = useRef<HTMLHeadingElement>(null);
  const endRef = useRef<HTMLHeadingElement>(null);

  // Focus the job heading when it appears, then the outcome when the job ends.
  useEffect(() => {
    (terminal || deleted ? endRef : headRef).current?.focus();
  }, [terminal, deleted]);

  async function remove(kind: "cancel" | "delete") {
    setBusy(true);
    setActionError(null);
    try {
      await deleteJob(job.id);
      if (kind === "delete") setDeleted(true);
    } catch (e) {
      setActionError(asApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const pct = Math.round(Math.min(1, Math.max(0, job.progress)) * 100);
  const running = job.steps.find((s) => s.status === "RUNNING");
  const results = job.outputs.filter((o) => o.role === "RESULT");
  const reports = job.outputs.filter((o) => o.role === "REPORT");
  const nInputs = job.inputs.length;
  const lost = error && error.err.status !== 404 && error.failures >= 3;
  const gone = error?.err.status === 404;

  let milestone = "Job queued.";
  if (deleted) milestone = "Deleted from the server.";
  else if (gone) milestone = errorTitle("JOB_NOT_FOUND", "");
  else if (job.status === "RUNNING")
    milestone = job.steps.length > 1 && running ? `Running step ${running.index + 1} of ${job.steps.length}.` : "Job started.";
  else if (job.status === "SUCCEEDED") milestone = "Done. The result is ready to download.";
  else if (job.status === "FAILED") milestone = `The job failed. ${errorTitle(job.error?.code ?? "", "")}`;
  else if (job.status === "CANCELLED") milestone = "Job cancelled.";

  const expiry = expiresIn(job.expiresAt, now);
  const outcome = job.status === "SUCCEEDED" ? outcomeOf(job, options) : null;
  // Before/after needs the original still in the browser: one PDF in, one PDF out.
  const original = files.length === 1 && nInputs === 1 && job.inputs[0].format === "pdf" ? files[0] : undefined;
  const preview =
    PREVIEW_TOOLS.has(job.tool) && original && results.length === 1 && results[0].format === "pdf" ? results[0] : undefined;

  return (
    <section className="job" aria-labelledby="job-title">
      <p className="sr-only" role="status">
        {milestone}
      </p>
      <div className="job-head">
        <h2 className="job-title" id="job-title" tabIndex={-1} ref={headRef}>
          {toolTitle}, {nInputs} {nInputs === 1 ? "file" : "files"}
        </h2>
        <StatusBadge status={job.status} />
      </div>

      {job.status === "QUEUED" && !gone && (
        <>
          <p className="statement job-statement">Queued</p>
          <p className="muted">Waiting for a free worker.</p>
          <div className="bar indet" aria-hidden="true">
            <span />
          </div>
        </>
      )}

      {job.status === "RUNNING" && !gone && (
        <>
          <div className="pct-row">
            <span className="pct" aria-hidden="true">
              {pct}%
            </span>
            {running?.message && <span className="muted">{running.message}</span>}
          </div>
          <div className="bar" role="progressbar" aria-label="Job progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ "--p": pct / 100 } as CSSProperties} />
          </div>
        </>
      )}

      {!terminal && !gone && job.steps.length > 0 && (
        <ol className="steps">
          {job.steps.map((s) => {
            const state = s.status === "SUCCEEDED" ? "done" : s.status === "RUNNING" ? "run" : "wait";
            return (
              <li key={s.index} className={state === "wait" ? "wait" : undefined}>
                {state === "done" ? (
                  <span className="step-done" aria-hidden="true">
                    <Icon name="check" size={14} />
                  </span>
                ) : state === "run" ? (
                  <span className="spinner" aria-hidden="true" />
                ) : (
                  <span className="step-wait" aria-hidden="true" />
                )}
                <span>
                  <span className="st">
                    <span className="sr-only">{state === "done" ? "Done: " : state === "run" ? "Running: " : "Waiting: "}</span>
                    {job.steps.length === 1 && !stepLabels[0] ? toolTitle : `Step ${s.index + 1}: ${stepLabels[s.index] ?? s.tool}`}
                  </span>
                  {s.message && <span className="sd">{s.message}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {lost && (
        <p className="notice job-gap" role="status">
          <Icon name="alert" size={18} />
          <span>Lost contact with FileX. Still trying…</span>
        </p>
      )}

      {!terminal && !gone && (
        <div className="actions">
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => remove("cancel")}>
            Cancel job
          </button>
        </div>
      )}

      {actionError && (
        <div className="job-gap">
          <Alert title={errorTitle(actionError.code, "That did not work.")} error={actionError} />
        </div>
      )}

      {gone && !deleted && (
        <div className="job-gap">
          <Alert title={errorTitle("JOB_NOT_FOUND", "")} error={error?.err}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={onStartOver}>
              Start over
            </button>
          </Alert>
        </div>
      )}

      {job.status === "FAILED" && (
        <div className="job-gap">
          <h3 className="sr-only" tabIndex={-1} ref={endRef}>
            The job failed
          </h3>
          <Alert
            title={errorTitle(job.error?.code ?? "", "The job failed.")}
            error={job.error ? new ApiError(0, job.error.code, job.error.message) : undefined}
          >
            <button type="button" className="btn btn-secondary btn-sm" onClick={onStartOver}>
              Try again
            </button>
          </Alert>
        </div>
      )}

      {job.status === "CANCELLED" && (
        <div className="result">
          <h3 className="statement" tabIndex={-1} ref={endRef}>
            Cancelled
          </h3>
          <p className="muted">Nothing was kept. Your files are still in the list.</p>
          <div className="actions">
            <button type="button" className="btn btn-secondary" onClick={onStartOver}>
              Start over
            </button>
          </div>
        </div>
      )}

      {job.status === "SUCCEEDED" && (
        <div className="result">
          <div className="res-head">
            <span className="res-ok" aria-hidden="true">
              <Icon name="check" size={20} />
            </span>
            <h3 className="job-title" tabIndex={-1} ref={endRef}>
              {deleted
                ? "Deleted from the server"
                : results.length === 1
                  ? `${results[0].name} is ready`
                  : `${results.length} files are ready`}
            </h3>
            {!deleted && <TargetBadge target={outcome?.target ?? null} />}
          </div>

          {deleted ? (
            <>
              <p className="muted job-gap">The result and your uploads are gone from the server.</p>
              <div className="actions">
                <button type="button" className="btn btn-secondary" onClick={onStartOver}>
                  Start over
                </button>
              </div>
            </>
          ) : (
            <>
              {outcome && <OutcomeBlock outcome={outcome} />}
              {preview && original && (
                <BeforeAfter original={original} jobId={job.id} output={preview} verb={job.tool === "pdf.compress" ? "compression" : "clean-up"} />
              )}
              {job.warnings.map((w, i) => (
                <div className="notice job-gap" role="note" key={i}>
                  <Icon name="alert" size={18} />
                  <p>
                    {warningText(w.code, w.message)} <span className="code">[{w.code}]</span>
                  </p>
                </div>
              ))}
              {results.length > 1 && <OutputList outputs={results} jobId={job.id} onError={setActionError} />}
              {reports.length > 0 && (
                <>
                  <h4 className="list-head section-title">Reports</h4>
                  <OutputList outputs={reports} jobId={job.id} onError={setActionError} />
                </>
              )}
              <div className="actions">
                {results.length > 0 && (
                  <DownloadButton className="btn btn-primary" jobId={job.id} onError={setActionError}>
                    <Icon name="download" size={20} />
                    {results.length === 1
                      ? `Download (${formatBytes(results[0].size)})`
                      : `Download all (zip, ${formatBytes(results.reduce((n, o) => n + o.size, 0))})`}
                  </DownloadButton>
                )}
                <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => remove("delete")}>
                  <Icon name="trash" size={20} />
                  Delete now
                </button>
                <button type="button" className="btn-text" onClick={onStartOver}>
                  Start over
                </button>
                {expiry && (
                  <span className="expires fsize">
                    <Icon name="clock" size={16} />
                    Expires {expiry}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function OutputList({ outputs, jobId, onError }: { outputs: JobOutput[]; jobId: string; onError: (e: ApiError) => void }) {
  return (
    <ol className="files job-gap">
      {outputs.map((o) => (
        <li key={o.index}>
          <span className="fname" title={o.name}>
            {o.name}
          </span>
          <span className="fsize">{formatBytes(o.size)}</span>
          <DownloadButton className="btn btn-secondary btn-row" jobId={jobId} index={o.index} onError={onError}>
            <Icon name="download" size={18} />
            Download<span className="sr-only"> {o.name}</span>
          </DownloadButton>
        </li>
      ))}
    </ol>
  );
}
