// The Convert page (spec 9.11, design.md "Convert page"): drop files, see the detected format,
// pick a reachable target, check the planned route, run. The route comes from GET /convert/plan;
// the job from POST /convert/jobs, then the shared JobView shows the steps and the result.
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  createConvertJob,
  paths,
  type ConvertPlan,
  type ConvertSource,
  type Job,
  type ToolSummary,
} from "../api/client";
import { useApi } from "../api/useApi";
import { formatLabel, formatTitle, type FormatIndex } from "../catalog";
import { Alert, loadFailedTitle } from "../components/Alert";
import { FilePicker, type Item } from "../components/FilePicker";
import { Icon } from "../components/Icon";
import { JobView } from "../components/JobView";
import { RoutePreview } from "../components/RoutePreview";
import { TargetPicker } from "../components/TargetPicker";
import { detectSources, readConvertQuery, reachable, stepLabels, stepsText } from "../convert";
import { useLimits } from "../limits";
import { errorTitle, formatBytes } from "../messages";
import { Link } from "../router";

const KINDS = "documents, ebooks, images and PDFs";
const MAX_FILES = 50; // POST /convert/jobs takes up to 50 files (ADR-0005)

function andList(words: string[]): string {
  return words.length <= 1 ? (words[0] ?? "") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

export function ConvertPage({ formats }: { formats: FormatIndex }) {
  const [query] = useState(() => readConvertQuery(window.location.search));
  const list = useApi<ConvertSource[]>(paths.convertTargets());
  const [items, setItems] = useState<Item[]>([]);
  const [explore, setExplore] = useState(query.from);
  const [target, setTarget] = useState(query.to);
  const [uploaded, setUploaded] = useState<number | null>(null);
  const [problem, setProblem] = useState<{ title: string; error?: ApiError } | null>(null);
  const [run, setRun] = useState<{ job: Job; title: string; labels: string[]; files: File[] } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const upload = useRef<AbortController | null>(null);
  const limits = useLimits();

  useEffect(() => {
    document.title = "Convert · FileX";
    // A file dropped while uploading or watching the job must not make the browser leave the page.
    const stop = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      document.title = "FileX";
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
      upload.current?.abort(); // leaving the page cancels an unfinished upload
    };
  }, []);

  const sources = list.data ?? [];
  const detected = detectSources(
    items.map((i) => i.file.name),
    formats,
  ).ids;
  // With files, their formats decide; without, the "From" select previews a format.
  const from = items.length ? detected : explore ? [explore] : [];
  const missing = from.filter((f) => !sources.some((s) => s.src === f));
  const targets = missing.length ? [] : reachable(list.data, from);
  const to = target && !from.includes(target) ? target : "";
  const maxFiles = Math.min(MAX_FILES, limits.maxFiles);
  const plan = useApi<ConvertPlan>(
    from.length && to ? paths.convertPlan(from, to, Math.max(1, items.length)) : null,
  );
  const uploading = uploaded !== null;
  const total = items.reduce((n, i) => n + i.file.size, 0);
  const ready = items.length > 0 && !!plan.data && !uploading;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || !plan.data) return;
    setProblem(null);
    if (total > limits.maxBytes) {
      setProblem({ title: `The files add up to ${formatBytes(total)}; one job takes up to ${formatBytes(limits.maxBytes)}.` });
      return;
    }
    const route = plan.data;
    const files = items.map((i) => i.file);
    const ac = new AbortController();
    upload.current = ac;
    setUploaded(0);
    setAnnouncement("Uploading.");
    try {
      const job = await createConvertJob(files, route.target, (loaded) => setUploaded(loaded), ac.signal);
      setRun({ job, title: `Convert to ${formatLabel(route.target)}`, labels: stepLabels(route.steps), files });
    } catch (err) {
      if (ac.signal.aborted) {
        setAnnouncement("Upload cancelled.");
      } else {
        const error = err instanceof ApiError ? err : new ApiError(0, "CLIENT", String(err));
        setProblem({ title: errorTitle(error.code, "Could not start the job."), error });
        plan.retry(); // the workers online may have changed: show the route as it is now
      }
    } finally {
      upload.current = null;
      setUploaded(null);
    }
  }

  const head = (
    <div className="tool-head field-surface folded cat-convert">
      <nav className="crumb" aria-label="Breadcrumb">
        <Link to="/">Tools</Link> / Convert
      </nav>
      <div className="tool-title-row">
        <h1 className="tool-title">Convert</h1>
        <p>Any format FileX can reach, up to {maxFiles} files in one job</p>
      </div>
    </div>
  );

  if (run) {
    return (
      <>
        {head}
        <div className="tool-run">
          <JobView
            key={run.job.id}
            initial={run.job}
            toolTitle={run.title}
            stepLabels={run.labels}
            files={run.files}
            onStartOver={() => setRun(null)}
          />
        </div>
      </>
    );
  }

  let body;
  if (list.loading) {
    body = (
      <p className="state" role="status">
        Loading the formats FileX can convert…
      </p>
    );
  } else if (list.error) {
    body = (
      <div className="state">
        <Alert title={loadFailedTitle(list.error, "the conversions")} error={list.error}>
          <button type="button" className="btn btn-secondary btn-sm" onClick={list.retry}>
            Try again
          </button>
        </Alert>
      </div>
    );
  } else if (sources.length === 0) {
    body = (
      <div className="state">
        <p className="statement">Nothing converts on this server right now</p>
        <AddonHint formats={formats} missing={[]} />
      </div>
    );
  } else {
    body = (
      <form className="tool-grid conv-grid" noValidate onSubmit={submit} aria-label="Convert">
        <p className="sr-only" role="status">
          {announcement}
        </p>
        <div>
          <FilePicker
            tool={{ title: "Convert", category: "convert", inputs: [...formats.keys()], files: { min: 1, max: maxFiles, each: true, aux: {} } }}
            kinds={KINDS}
            formats={formats}
            items={items}
            onItems={(next) => {
              setItems(next);
              setProblem(null);
            }}
            uploaded={uploaded}
            onCancelUpload={() => upload.current?.abort()}
            announce={setAnnouncement}
            locked={uploading}
          />

          <section className="conv-to" aria-labelledby="to-title">
            <h2 className="section-title" id="to-title">
              Convert to
            </h2>
            <From
              formats={formats}
              sources={sources}
              detected={detected}
              names={items.map((i) => i.file.name)}
              value={explore}
              disabled={uploading}
              onChange={setExplore}
            />
            {from.length === 0 ? (
              <p className="help">Add files, or choose a format above, to see what it can become.</p>
            ) : missing.length ? (
              <AddonHint formats={formats} missing={missing} />
            ) : targets.length === 0 ? (
              <Alert title="These files have no format in common.">
                <p>
                  {andList(from.map(formatLabel))} files cannot be converted to the same format. Remove some files or
                  convert them in separate jobs.
                </p>
              </Alert>
            ) : (
              <TargetPicker
                targets={targets}
                formats={formats}
                value={to}
                disabled={uploading}
                onChange={(id) => {
                  setTarget(id);
                  setProblem(null);
                }}
              />
            )}
          </section>
        </div>

        <div className="options-col">
          <section className="options route-panel" aria-labelledby="route-title">
            <h2 className="section-title" id="route-title">
              Route
            </h2>
            <RouteState from={from} to={to} plan={plan} formats={formats} files={items.length} />
          </section>
          <div className="submit-bar">
            {problem && <Alert title={problem.title} error={problem.error} />}
            <p className="fsize">
              {uploading
                ? `Uploading ${total ? Math.round(((uploaded ?? 0) / total) * 100) : 0}% of ${formatBytes(total)}`
                : items.length === 0
                  ? "Add files to start"
                  : `${items.length} ${items.length === 1 ? "file" : "files"}, ${formatBytes(total)}` +
                    (!to ? " · choose a format" : plan.loading ? " · planning the route" : plan.error ? " · no route" : "")}
            </p>
            {uploading ? (
              <button type="button" className="btn btn-secondary btn-block" onClick={() => upload.current?.abort()}>
                <Icon name="x" size={20} />
                Cancel upload
              </button>
            ) : (
              <button type="submit" className="btn btn-primary btn-block" disabled={!ready}>
                {ready && plan.data
                  ? `Convert ${items.length === 1 ? "1 file" : `${items.length} files`} to ${formatLabel(plan.data.target)}`
                  : "Convert"}{" "}
                <span aria-hidden="true">→</span>
              </button>
            )}
          </div>
        </div>
      </form>
    );
  }

  return (
    <>
      {head}
      <p className="tool-summary">
        Drop files and pick a format. When no single converter does the job, FileX chains them, across workers if it
        has to, and shows you the route first.
      </p>
      {body}
    </>
  );
}

/** "From": the detected formats once there are files; a select to preview a format before that. */
function From({ formats, sources, detected, names, value, disabled, onChange }: {
  formats: FormatIndex;
  sources: ConvertSource[];
  detected: string[];
  names: string[];
  value: string;
  disabled: boolean;
  onChange: (id: string) => void;
}) {
  if (names.length > 0) {
    return (
      <div className="from">
        <span className="eyebrow">From</span>
        <ul className="from-list">
          {detected.map((id) => {
            const n = names.filter((name) => detectSources([name], formats).ids[0] === id).length;
            return (
              <li key={id}>
                <span className="fmt">{formatLabel(id)}</span> {formatTitle(id, formats)}
                <span className="fsize"> · {n === 1 ? "1 file" : `${n} files`}</span>
              </li>
            );
          })}
        </ul>
        <p className="help">Read from the file names. FileX checks the content when it uploads.</p>
      </div>
    );
  }
  const known = sources.some((s) => s.src === value);
  return (
    <div className="from">
      <label className="eyebrow" htmlFor="conv-from">
        From
      </label>
      <select
        id="conv-from"
        className="input"
        value={value}
        disabled={disabled}
        aria-describedby="conv-from-help"
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Choose a format</option>
        {value && !known && <option value={value}>{formatLabel(value)} · {formatTitle(value, formats)}</option>}
        {sources.map((s) => (
          <option key={s.src} value={s.src}>
            {formatLabel(s.src)} · {formatTitle(s.src, formats)}
          </option>
        ))}
      </select>
      <p className="help" id="conv-from-help">
        To preview a route before adding files. Files you add decide the format.
      </p>
    </div>
  );
}

/** Profiles the server says would open a route, as the COMPOSE_PROFILES instruction. */
function ProfileHint({ profiles }: { profiles: string[] }) {
  const extra = profiles.filter((p) => p !== "core");
  return (
    <>
      {profiles.includes("core") && <p>Start the core worker.</p>}
      {extra.length > 0 && (
        <p>
          Enable the {andList(extra)} {extra.length === 1 ? "add-on" : "add-ons"}: add{" "}
          <code>{extra.join(",")}</code> to <code>COMPOSE_PROFILES</code> in <code>.env</code>, then run{" "}
          <code>docker compose up -d</code>.
        </p>
      )}
    </>
  );
}

/**
 * No converter online takes these formats. GET /tools says which tools are off and in which
 * profile, so the hint can name the add-on that would help.
 */
function AddonHint({ formats, missing }: { formats: FormatIndex; missing: string[] }) {
  const { data: tools } = useApi<ToolSummary[]>(paths.tools());
  const off = (tools ?? []).filter(
    (t) =>
      !t.available &&
      (t.category === "convert" || t.category === "ebook") &&
      (missing.length === 0 || t.inputs.some((f) => missing.includes(f))),
  );
  const profiles = [...new Set(off.map((t) => t.profile))].sort();
  const names = andList(missing.map((id) => formatTitle(id, formats)));
  return (
    <div className="notice conv-gap" role="note">
      <Icon name="lock" size={18} />
      <div>
        <strong>
          {missing.length ? `Nothing on this server converts ${names} files right now.` : "No converter is running."}
        </strong>
        {profiles.length > 0 ? (
          <ProfileHint profiles={profiles} />
        ) : (
          <p>{tools ? "No add-on on this server takes them either." : ""}</p>
        )}
      </div>
    </div>
  );
}

function RouteState({ from, to, plan, formats, files }: {
  from: string[];
  to: string;
  plan: ReturnType<typeof useApi<ConvertPlan>>;
  formats: FormatIndex;
  files: number;
}) {
  if (from.length === 0) {
    return <p className="help">The route shows here once FileX knows what your files are.</p>;
  }
  if (!to) return <p className="help">Choose a format to see how FileX gets there.</p>;
  if (plan.loading) {
    return (
      <p className="help" role="status">
        Planning the route…
      </p>
    );
  }
  const err = plan.error;
  if (err?.code === "NO_ROUTE") {
    const profiles = Array.isArray(err.details.profiles)
      ? err.details.profiles.filter((p): p is string => typeof p === "string" && /^[a-z0-9-]{1,32}$/.test(p))
      : [];
    return (
      <Alert title={`No route from ${andList(from.map(formatLabel))} to ${formatLabel(to)} right now.`}>
        {profiles.length > 0 ? (
          <ProfileHint profiles={profiles} />
        ) : from.length > 1 ? (
          <p>
            {andList(from.map(formatLabel))} files share no first step. Convert them in separate jobs, or pick another
            format.
          </p>
        ) : (
          <p>No converter on this server makes {formatTitle(to, formats)} from {formatTitle(from[0], formats)}. Pick another format.</p>
        )}
        <p>
          <span className="code">[NO_ROUTE]</span>
        </p>
      </Alert>
    );
  }
  if (err?.code === "TOO_MANY_FILES") {
    const max = Number(err.details.max);
    const n = Number.isFinite(max) && max >= 1 ? Math.trunc(max) : null;
    return (
      <Alert title="Too many files for this route." error={err}>
        {n !== null && (
          <p>
            One step takes at most {n} {n === 1 ? "file" : "files"}. Remove {files - n} or convert them in separate jobs.
          </p>
        )}
      </Alert>
    );
  }
  if (err) {
    return (
      <Alert title={errorTitle(err.code, "Could not plan the route.")} error={err}>
        <button type="button" className="btn btn-secondary btn-sm" onClick={plan.retry}>
          Try again
        </button>
      </Alert>
    );
  }
  if (!plan.data) return null;
  const route = plan.data;
  return (
    <>
      <p className="sr-only" role="status">
        Route ready: {stepsText(route.steps.length)}
        {route.lossy ? ", some formatting may be lost." : "."}
      </p>
      <RoutePreview plan={route} formats={formats} />
    </>
  );
}
