// The only place that talks to the FileX API. Types come from the generated schema.d.ts
// (`npm run gen:api` after docs/api/openapi.json changes); never hand-write API types.
import type { components } from "./schema";

export type ToolSummary = components["schemas"]["ToolSummary"];
export type ToolDetail = components["schemas"]["ToolDetail"];
export type Format = components["schemas"]["Format"];
export type Job = components["schemas"]["Job"];
export type JobOutput = components["schemas"]["JobOutput"];
export type JobList = components["schemas"]["JobList"];
export type Config = components["schemas"]["Config"];
export type Limits = components["schemas"]["ConfigLimits"];
export type ConvertSource = components["schemas"]["ConvertSource"];
export type ConvertTarget = components["schemas"]["ConvertTarget"];
export type ConvertPlan = components["schemas"]["ConvertPlan"];
export type ConvertStep = components["schemas"]["ConvertStep"];
type DownloadLink = components["schemas"]["DownloadLink"];
type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];

const BASE = "/api/v1";

// Auth seam: the Keel access token (or an fxt_ token) comes from here and goes only into the
// Authorization header, never into a URL, a body or a log. Open mode (FILEX_AUTH=none) sends none.
let tokenProvider: () => string | null = () => null;
export function setTokenProvider(fn: () => string | null): void {
  tokenProvider = fn;
}

// The sign-in gate hears about 401, 403 USER_NOT_ALLOWED and 503 AUTH_UNAVAILABLE from any call.
const AUTH_CODES = new Set(["UNAUTHENTICATED", "USER_NOT_ALLOWED", "AUTH_UNAVAILABLE"]);
let onAuthError: (e: ApiError) => void = () => {};
export function setAuthErrorHandler(fn: (e: ApiError) => void): void {
  onAuthError = fn;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

function authHeaders(): Record<string, string> {
  const token = tokenProvider();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function errorFrom(status: number, body: ErrorEnvelope | null): ApiError {
  const err = new ApiError(
    status,
    body?.error?.code ?? "HTTP_ERROR",
    body?.error?.message ?? `The server answered ${status}.`,
    body?.error?.details ?? {},
  );
  if (status === 401 || AUTH_CODES.has(err.code)) onAuthError(err);
  return err;
}

const NETWORK_MESSAGE = "FileX did not answer. Check that the server is running.";

// Job ids come from the server; only a well-formed id may become part of a path (spec 9.14).
const JOB_ID = /^j_[A-Za-z0-9_-]{22}$/;
function jobPath(id: string): string {
  if (!JOB_ID.test(id)) throw new ApiError(0, "CLIENT", "The server sent a malformed job id.");
  return `/jobs/${id}`;
}

/** FileX API paths only; ids are encoded so they cannot change the path. */
export const paths = {
  tools: () => "/tools",
  tool: (id: string) => `/tools/${encodeURIComponent(id)}`,
  formats: () => "/formats",
  config: () => "/config",
  status: () => "/status",
  jobs: (toolId: string) => `/tools/${encodeURIComponent(toolId)}/jobs`,
  job: (id: string) => jobPath(id),
  myJobs: (before?: number | null) =>
    before == null ? "/jobs?limit=20" : `/jobs?limit=20&before=${Math.trunc(before)}`,
  convertTargets: () => "/convert/targets",
  convertPlan: (sources: string[], to: string, files: number) => {
    const q = new URLSearchParams();
    for (const s of sources) q.append("from", s);
    q.set("to", to);
    q.set("files", String(Math.max(1, Math.trunc(files))));
    return `/convert/plan?${q}`;
  },
  convertJobs: () => "/convert/jobs",
};

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await send(path, { signal });
  if (res.ok) return (await res.json()) as T;
  throw errorFrom(res.status, await res.json().catch(() => null));
}

async function send(path: string, init: RequestInit & { headers?: Record<string, string> }) {
  const headers = { Accept: "application/json", ...authHeaders(), ...init.headers };
  try {
    return await fetch(BASE + path, { ...init, headers, credentials: "omit", cache: "no-store" });
  } catch (e) {
    if (init.signal?.aborted) throw e;
    throw new ApiError(0, "NETWORK", NETWORK_MESSAGE);
  }
}

export type JobRequest = {
  files: File[];
  aux: Record<string, File>;
  options: Record<string, unknown>;
  /** Converters only (ADR-0005): the output format id. Left out, the API picks the default. */
  target?: string;
};

/**
 * POST a job as multipart. XMLHttpRequest because fetch has no upload progress. Options go as
 * a plain field (no filename), so the part does not count against the file limit.
 */
export function createJob(
  toolId: string,
  req: JobRequest,
  onProgress: (loaded: number, total: number) => void,
  signal: AbortSignal,
): Promise<Job> {
  const form = new FormData();
  for (const f of req.files) form.append("files", f, f.name);
  for (const [slot, f] of Object.entries(req.aux)) form.append(slot, f, f.name);
  form.append("options", JSON.stringify(req.options));
  if (req.target) form.append("target", req.target);
  return postJob(paths.jobs(toolId), form, onProgress, signal);
}

/** POST /convert/jobs (ADR-0005): the files and the target format; no options, no aux files. */
export function createConvertJob(
  files: File[],
  target: string,
  onProgress: (loaded: number, total: number) => void,
  signal: AbortSignal,
): Promise<Job> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.append("target", target);
  return postJob(paths.convertJobs(), form, onProgress, signal);
}

function postJob(
  path: string,
  form: FormData,
  onProgress: (loaded: number, total: number) => void,
  signal: AbortSignal,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", BASE + path);
    xhr.responseType = "json";
    xhr.setRequestHeader("Accept", "application/json");
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status === 202 && xhr.response?.id) resolve(xhr.response as Job);
      else reject(errorFrom(xhr.status, xhr.response as ErrorEnvelope | null));
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK", NETWORK_MESSAGE));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    if (signal.aborted) return reject(new DOMException("Upload cancelled", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

/** Poll a job. `job` is undefined when the server answered 304 for the `etag` we sent. */
export async function pollJob(id: string, etag: string | null, signal: AbortSignal) {
  const res = await send(paths.job(id), { signal, headers: etag ? { "If-None-Match": etag } : {} });
  if (res.status === 304) return { job: undefined, etag };
  if (!res.ok) throw errorFrom(res.status, await res.json().catch(() => null));
  return { job: (await res.json()) as Job, etag: res.headers.get("ETag") };
}

/** Cancel a queued or running job, or delete a finished one now. */
export async function deleteJob(id: string): Promise<void> {
  const res = await send(paths.job(id), { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw errorFrom(res.status, await res.json().catch(() => null));
}

/** Forget the API's cached check of this token (ADR-0009); called before Keel's signOut(). */
export async function deleteSession(): Promise<void> {
  const res = await send("/session", { method: "DELETE" });
  if (!res.ok) throw errorFrom(res.status, await res.json().catch(() => null));
}

// The only download URL the UI ever opens: the API's own signed /dl/ path, same origin.
const DL_URL = /^\/api\/v1\/dl\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\?[A-Za-z0-9_=&%.-]+$/;

/**
 * Mint a fresh 5-minute download link on every click (never cached, every auth mode). The
 * browser then streams /dl/... to disk with no Authorization header; nothing goes into a Blob.
 */
export async function mintDownload(jobId: string, index?: number): Promise<string> {
  const target = index === undefined ? "/result/link" : `/outputs/${Math.trunc(index)}/link`;
  const res = await send(paths.job(jobId) + target, { method: "POST" });
  if (!res.ok) throw errorFrom(res.status, await res.json().catch(() => null));
  const { url } = (await res.json()) as DownloadLink;
  if (typeof url !== "string" || !DL_URL.test(url)) {
    throw new ApiError(0, "CLIENT", "The server sent a download link FileX does not trust.");
  }
  return url;
}

/**
 * The bytes of one output, for the client-side preview only (spec 9.7): a fresh signed link,
 * fetched same-origin. The caller checks the output's size first; downloads never come here.
 */
export async function fetchOutput(jobId: string, index: number, signal: AbortSignal): Promise<ArrayBuffer> {
  const url = await mintDownload(jobId, index);
  const res = await fetch(url, { signal, credentials: "omit", cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, "HTTP_ERROR", `The server answered ${res.status}.`);
  return res.arrayBuffer();
}

/** Navigation seam (tests replace it): an attachment response downloads and the page stays. */
export const browser = {
  open(url: string): void {
    window.location.assign(url);
  },
};
