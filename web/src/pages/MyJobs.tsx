// My jobs (spec 9.10, design.md "Job list"): the caller's own jobs from GET /jobs, newest first,
// with "Show older" for the next page. Names come from the job and are rendered as text.
import { useEffect, useState } from "react";
import { ApiError, apiGet, paths, type Job, type JobList, type ToolSummary } from "../api/client";
import { useApi } from "../api/useApi";
import { Alert, loadFailedTitle } from "../components/Alert";
import { DownloadButton } from "../components/DownloadButton";
import { Icon } from "../components/Icon";
import { StatusBadge, useNow } from "../components/JobView";
import { formatLabel } from "../catalog";
import { convertTarget } from "../convert";
import { errorTitle, expiresIn } from "../messages";
import { Link } from "../router";

const WHEN = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function when(iso: string | null): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? WHEN.format(t) : "";
}

function inputsText(job: Job): string {
  const n = job.inputs.length;
  if (n === 0) return "";
  return n === 1 ? job.inputs[0].name : `${n} files`;
}

export function MyJobs() {
  const first = useApi<JobList>(paths.myJobs());
  const { data: tools } = useApi<ToolSummary[]>(paths.tools());
  const [older, setOlder] = useState<{ items: Job[]; next: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiError | null>(null);
  const now = useNow(60_000);

  useEffect(() => {
    document.title = "My jobs · FileX";
    return () => {
      document.title = "FileX";
    };
  }, []);

  const items = [...(first.data?.items ?? []), ...(older?.items ?? [])];
  const next = older ? older.next : (first.data?.nextBefore ?? null);
  const title = (id: string) => tools?.find((t) => t.id === id)?.title ?? id;
  // A convert job's tool is "convert"; its title names the format it makes.
  const jobTitle = (job: Job) => {
    if (job.tool !== "convert") return title(job.tool);
    const to = convertTarget(job, tools);
    return to ? `Convert to ${formatLabel(to)}` : "Convert";
  };

  async function loadOlder() {
    if (next === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const page = await apiGet<JobList>(paths.myJobs(next));
      setOlder((o) => ({ items: [...(o?.items ?? []), ...page.items], next: page.nextBefore }));
    } catch (e) {
      setProblem(e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e)));
    } finally {
      setBusy(false);
    }
  }

  let body;
  if (first.loading) body = <p className="state" role="status">Loading your jobs…</p>;
  else if (first.error) {
    body = (
      <div className="state">
        <Alert title={loadFailedTitle(first.error, "your jobs")} error={first.error}>
          <button type="button" className="btn btn-secondary btn-sm" onClick={first.retry}>
            Try again
          </button>
        </Alert>
      </div>
    );
  } else if (items.length === 0) {
    body = (
      <div className="state">
        <p className="statement">No jobs yet</p>
        <p className="muted">Jobs you run show up here until they expire.</p>
        <Link className="btn btn-secondary" to="/">See all tools</Link>
      </div>
    );
  } else {
    body = (
      <>
        <ul className="jobs">
          {items.map((job) => {
            const expiry = expiresIn(job.expiresAt, now);
            const hasResult = job.status === "SUCCEEDED" && job.outputs.some((o) => o.role === "RESULT");
            return (
              <li key={job.id}>
                <span className="job-name">
                  <Link to={job.tool === "convert" ? "/convert" : `/tools/${encodeURIComponent(job.tool)}`}>
                    {job.tool === "convert" ? <ConvertName to={convertTarget(job, tools)} /> : title(job.tool)}
                  </Link>{" "}
                  <span className="muted">{inputsText(job)}</span>
                </span>
                <StatusBadge status={job.status} />
                <span className="meta when">{when(job.createdAt)}</span>
                <span className="meta when">{expiry ? `Expires ${expiry}` : ""}</span>
                {hasResult ? (
                  <DownloadButton className="btn btn-secondary btn-row" jobId={job.id} onError={setProblem}>
                    <Icon name="download" size={18} />
                    Download<span className="sr-only"> result of {jobTitle(job)}, {when(job.createdAt)}</span>
                  </DownloadButton>
                ) : (
                  <span aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ul>
        {next !== null && (
          <div className="actions">
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={loadOlder}>
              {busy ? "Loading…" : "Show older jobs"}
            </button>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="main-jobs">
      <h1 className="page-title">My jobs</h1>
      {problem && (
        <div className="state">
          <Alert title={errorTitle(problem.code, "That did not work.")} error={problem} />
        </div>
      )}
      {body}
    </div>
  );
}

/** "Convert → PDF" on screen, "Convert to PDF" for screen readers. */
function ConvertName({ to }: { to: string | null }) {
  if (!to) return <>Convert</>;
  return (
    <>
      Convert<span aria-hidden="true"> → </span>
      <span className="sr-only"> to </span>
      {formatLabel(to)}
    </>
  );
}
