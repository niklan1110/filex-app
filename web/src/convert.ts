// Convert page presentation (design.md "Convert page", spec 9.11): format families, the targets
// several files can share, worker names, popular pairs and job titles. Pure, so it is unit-tested.
import type { ConvertSource, ConvertStep, ConvertTarget, Job, ToolSummary } from "./api/client";
import { formatLabel, sourceFormat, type FormatIndex } from "./catalog";

export const FAMILIES = [
  { id: "pdf", title: "PDF" },
  { id: "documents", title: "Documents" },
  { id: "ebooks", title: "Ebooks & comics" },
  { id: "images", title: "Images" },
  { id: "other", title: "Other" },
] as const;
export type FamilyId = (typeof FAMILIES)[number]["id"];

const FAMILY_OF: Record<string, FamilyId> = {
  pdf: "pdf",
  ...Object.fromEntries(
    ["docx", "doc", "odt", "rtf", "xlsx", "xls", "ods", "csv", "pptx", "ppt", "odp", "txt", "md", "html", "json"].map(
      (id) => [id, "documents" as const],
    ),
  ),
  ...Object.fromEntries(["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"].map((id) => [id, "ebooks" as const])),
  ...Object.fromEntries(["jpg", "png", "webp", "tiff", "heic", "gif", "bmp"].map((id) => [id, "images" as const])),
};

export function family(id: string): FamilyId {
  return FAMILY_OF[id] ?? "other";
}

/** Format ids that may go into a URL or a query: what the server's ids look like. */
export const FORMAT_ID = /^[a-z0-9.-]{1,32}$/;

/**
 * The targets every one of `sources` reaches (the intersection of their single-file lists): steps
 * is the longest route, lossy if any route is lossy, groups the union. The planner has the final
 * word for mixed files, whose first step must be one tool (GET /convert/plan answers NO_ROUTE).
 */
export function reachable(list: ConvertSource[] | undefined, sources: string[]): ConvertTarget[] {
  if (!list || sources.length === 0) return [];
  const per = sources.map((s) => list.find((x) => x.src === s)?.targets ?? []);
  return per[0]
    .filter((t) => !sources.includes(t.format))
    .flatMap((t) => {
      const all = per.map((ts) => ts.find((x) => x.format === t.format));
      if (all.some((x) => !x)) return [];
      const found = all as ConvertTarget[];
      return [
        {
          format: t.format,
          steps: Math.max(...found.map((x) => x.steps)),
          lossy: found.some((x) => x.lossy),
          groups: [...new Set(found.flatMap((x) => x.groups))].sort(),
        },
      ];
    });
}

/** Targets grouped by family in FAMILIES order; empty families are left out. */
export function byFamily<T extends { format: string }>(targets: T[]): { id: FamilyId; title: string; targets: T[] }[] {
  return FAMILIES.map((f) => ({ ...f, targets: targets.filter((t) => family(t.format) === f.id) })).filter(
    (f) => f.targets.length > 0,
  );
}

/** "core worker" or "office add-on": a worker group is a compose profile (ADR-0002). */
export function workerName(group: string): string {
  return group === "core" ? "core worker" : `${group} add-on`;
}

export function stepsText(n: number): string {
  return `${n} ${n === 1 ? "step" : "steps"}`;
}

/** The distinct source formats of the file names, in the order they first appear. */
export function detectSources(names: string[], formats: FormatIndex): { ids: string[]; unknown: string[] } {
  const all = [...formats.keys()];
  const ids: string[] = [];
  const unknown: string[] = [];
  for (const n of names) {
    const id = sourceFormat(n, all, formats);
    if (id === null) unknown.push(n);
    else if (!ids.includes(id)) ids.push(id);
  }
  return { ids, unknown };
}

/** "DOCX + ODT" for the first node of a mixed route. */
export function formatsLabel(ids: string[]): string {
  return ids.map(formatLabel).join(" + ");
}

/** One line per step for the job's step list: "DOCX to PDF (Office to PDF)". */
export function stepLabels(steps: ConvertStep[]): string[] {
  return steps.map((s) => `${s.src.map(formatLabel).join(" and ")} to ${formatLabel(s.dst)} (${s.title})`);
}

// Names people search for, for the popular-conversion links.
const SHORT: Record<string, string> = {
  docx: "Word",
  doc: "Word 97",
  xlsx: "Excel",
  xls: "Excel 97",
  pptx: "PowerPoint",
  ppt: "PowerPoint 97",
  md: "Markdown",
  txt: "Text",
  html: "HTML",
  jpg: "JPG",
  png: "PNG",
  heic: "HEIC",
};

export function shortName(id: string): string {
  return SHORT[id] ?? formatLabel(id);
}

/** Common pairs, most wanted first. Only the ones reachable now are shown. */
export const POPULAR: [string, string][] = [
  ["docx", "pdf"],
  ["xlsx", "pdf"],
  ["pptx", "pdf"],
  ["jpg", "pdf"],
  ["pdf", "jpg"],
  ["png", "pdf"],
  ["heic", "pdf"],
  ["md", "epub"],
  ["epub", "pdf"],
  ["md", "pdf"],
  ["html", "pdf"],
  ["pdf", "txt"],
  ["epub", "mobi"],
  ["odt", "docx"],
];

export type Pair = { from: string; to: string; lossy: boolean; steps: number };

export function popularPairs(list: ConvertSource[] | undefined, max = 8): Pair[] {
  if (!list) return [];
  const out: Pair[] = [];
  for (const [from, to] of POPULAR) {
    const t = list.find((s) => s.src === from)?.targets.find((x) => x.format === to);
    if (t) out.push({ from, to, lossy: t.lossy, steps: t.steps });
    if (out.length === max) break;
  }
  return out;
}

/** Search text for a pair: "word docx word document to pdf pdf". */
export function pairText(p: Pair, formats: FormatIndex): string {
  return [p.from, shortName(p.from), formats.get(p.from)?.title ?? "", p.to, shortName(p.to), formats.get(p.to)?.title ?? ""]
    .join(" ")
    .toLowerCase();
}

export function convertHref(from?: string, to?: string): string {
  const q = new URLSearchParams();
  if (from && FORMAT_ID.test(from)) q.set("from", from);
  if (to && FORMAT_ID.test(to)) q.set("to", to);
  const s = q.toString();
  return s ? `/convert?${s}` : "/convert";
}

/** ?from= and ?to= of the current address, only when they look like format ids. */
export function readConvertQuery(search: string): { from: string; to: string } {
  const q = new URLSearchParams(search);
  const ok = (v: string | null) => (v && FORMAT_ID.test(v) ? v : "");
  return { from: ok(q.get("from")), to: ok(q.get("to")) };
}

/**
 * The format a convert job makes: its first result's format, else the single output of the
 * tool that runs its last step, else null (a running job has no outputs yet).
 */
export function convertTarget(job: Job, tools: Pick<ToolSummary, "id" | "outputs">[] | undefined): string | null {
  const out = job.outputs.find((o) => o.role === "RESULT");
  if (out) return out.format;
  const last = job.steps[job.steps.length - 1];
  const outs = last ? tools?.find((t) => t.id === last.tool)?.outputs : undefined;
  return outs?.length === 1 ? outs[0] : null;
}
