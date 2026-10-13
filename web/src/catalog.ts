// Catalog presentation: category order and names, tool icons, search. Pure, so it is unit-tested.
import type { Format, ToolSummary } from "./api/client";
import type { IconName } from "./components/Icon";

/** Spec section 6 order. `band` heads a field, `chip` labels the filter button. */
export const CATEGORIES: { id: string; band: string; chip: string; icon: IconName }[] = [
  { id: "organize", band: "Organize", chip: "Organize", icon: "grid" },
  { id: "optimize", band: "Optimize", chip: "Optimize", icon: "compress" },
  { id: "convert", band: "Convert", chip: "Convert", icon: "convert" },
  { id: "ocr", band: "OCR & scans", chip: "OCR & scans", icon: "scan" },
  { id: "edit", band: "Edit", chip: "Edit", icon: "pen" },
  { id: "security", band: "Security & privacy", chip: "Security", icon: "lock" },
  { id: "ebook", band: "Ebooks & comics", chip: "Ebooks", icon: "text" },
  { id: "compare", band: "Compare", chip: "Compare", icon: "eye" },
  { id: "ai", band: "AI", chip: "AI", icon: "sparkle" },
];

export function category(id: string) {
  return CATEGORIES.find((c) => c.id === id) ?? { id, band: id, chip: id, icon: "file" as IconName };
}

// The API has no icon field; tools without an entry use their category's icon.
const TOOL_ICONS: Record<string, IconName> = {
  "pdf.merge": "merge",
  "pdf.split": "split",
  "pdf.rotate": "rotate",
  "pdf.organize": "grid",
  "pdf.compress": "compress",
  "pdf.repair": "wrench",
  "pdf.watermark": "stamp",
  "pdf.page-numbers": "hash",
  "pdf.sign": "pen",
  "pdf.encrypt": "lock",
  "pdf.metadata-strip": "eraser",
  "scan.clean": "sparkle",
};

export function toolIcon(t: ToolSummary): IconName {
  return TOOL_ICONS[t.id] ?? category(t.category).icon;
}

export type FormatIndex = Map<string, Format>;

export function formatIndex(formats: Format[] | undefined): FormatIndex {
  return new Map((formats ?? []).map((f) => [f.id, f]));
}

export function formatTitle(id: string, formats: FormatIndex): string {
  return formats.get(id)?.title ?? id.toUpperCase();
}

function haystack(t: ToolSummary, formats: FormatIndex): string {
  const fmt = [...t.inputs, ...t.outputs].flatMap((id) => {
    const f = formats.get(id);
    return [id, f?.title ?? "", ...(f?.extensions ?? [])];
  });
  const cat = category(t.category);
  return [t.id, t.title, t.summary, cat.band, cat.chip, ...(t.keywords ?? []), ...fmt].join(" ").toLowerCase();
}

/**
 * Every word must appear in the tool's name, summary, id, keywords, category or formats. "to" is
 * dropped so "word to pdf" means "word" and "pdf". Synonyms come from the tool's keywords
 * (for example "combine" for merge).
 */
export function searchTools(tools: ToolSummary[], query: string, formats: FormatIndex): ToolSummary[] {
  const words = query.toLowerCase().split(/\s+/).filter((w) => w && w !== "to");
  if (words.length === 0) return tools;
  return tools.filter((t) => {
    const text = haystack(t, formats);
    return words.every((w) => text.includes(w));
  });
}

/** "PDF only, 2 to 50 files" for the tool header. */
export function acceptsInWords(
  inputs: string[],
  files: { min: number; max: number; each: boolean },
  formats: FormatIndex,
): string {
  if (files.max === 0) return "No files needed"; // cert.create makes its output from options
  const names = inputs.map((id) => formatTitle(id, formats));
  const what =
    names.length === 1
      ? `${names[0]} only`
      : names.length === 0
        ? "Any supported file"
        : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
  const count =
    files.max <= 1
      ? "one file"
      : files.each
        ? "many files at once"
        : `${files.min} to ${files.max} files`;
  return `${what}, ${count}`;
}

/** `accept` attribute for a file input: extensions plus MIME types of the given formats. */
export function acceptAttr(ids: string[], formats: FormatIndex): string {
  return ids.flatMap((id) => {
    const f = formats.get(id);
    return f ? [...f.extensions, f.mime] : [];
  }).join(",");
}

/**
 * Client-side hint only: the name's extension must belong to one of the formats. The server
 * sniffs the content and has the final word. With no format list loaded, everything passes.
 */
export function fileMatches(name: string, ids: string[], formats: FormatIndex): boolean {
  const exts = ids.flatMap((id) => formats.get(id)?.extensions ?? []);
  if (exts.length === 0) return true;
  const lower = name.toLowerCase();
  return exts.some((e) => lower.endsWith(e.toLowerCase()));
}

/** Short format label for converter UI: "DOCX", "PDF". */
export function formatLabel(id: string): string {
  return id.toUpperCase();
}

function listShort(ids: string[], cap = 3): string {
  const names = ids.map(formatLabel);
  return names.length <= cap ? names.join(", ") : `${names.slice(0, cap).join(", ")} +${names.length - cap}`;
}

/** "DOCX, XLSX, PPTX" → "PDF" for converter tiles and tool pages; null for other tools. */
export function convertsHint(t: { category: string; inputs: string[]; outputs: string[] }) {
  if (t.category !== "convert" || t.inputs.length === 0 || t.outputs.length === 0) return null;
  return { from: listShort(t.inputs), to: listShort(t.outputs) };
}

type Edge = { src: string; dst: string; lossy: boolean };
export type Target = { id: string; lossy: boolean };

/**
 * What the "Convert to" control should do for the chosen files (ADR-0005 target amendment):
 * - none: not a converter, or the tool's own option (targetOption) carries the target; send nothing.
 * - waiting: a converter with no files yet.
 * - auto: every file reaches exactly one format; the API picks it, send nothing.
 * - choose: several formats every file can reach; the user picks one, sent as `target`.
 * - conflict: the files share no format; `formats` names the source formats that clash.
 */
export type TargetPlan =
  | { kind: "none" }
  | { kind: "waiting" }
  | { kind: "auto"; target: Target }
  | { kind: "choose"; targets: Target[] }
  | { kind: "conflict"; formats: string[] };

/**
 * The file's format by extension, among the converter's source formats. The server sniffs the
 * content and has the final word (422 TARGET_REQUIRED / INVALID_TARGET cover any mismatch).
 */
export function sourceFormat(name: string, sources: string[], formats: FormatIndex): string | null {
  const lower = name.toLowerCase();
  let best: { id: string; len: number } | null = null;
  for (const id of sources) {
    const exts = formats.get(id)?.extensions ?? [`.${id}`];
    for (const e of exts) {
      if (lower.endsWith(e.toLowerCase()) && (!best || e.length > best.len)) best = { id, len: e.length };
    }
  }
  return best?.id ?? null;
}

export function planTarget(
  tool: { converts: Edge[]; targetOption: string | null },
  fileNames: string[],
  formats: FormatIndex,
): TargetPlan {
  if (tool.converts.length === 0 || tool.targetOption) return { kind: "none" };
  const sources = [...new Set(tool.converts.map((e) => e.src))];
  // Files whose extension says nothing are left to the server's sniffing.
  const srcs = [...new Set(fileNames.map((n) => sourceFormat(n, sources, formats)).filter((s) => s !== null))];
  if (srcs.length === 0) return { kind: "waiting" };
  const reach = srcs.map((s) => tool.converts.filter((e) => e.src === s));
  const targets: Target[] = reach[0]
    .filter((e, i, all) => all.findIndex((x) => x.dst === e.dst) === i)
    .filter((e) => reach.every((edges) => edges.some((x) => x.dst === e.dst)))
    .map((e) => ({ id: e.dst, lossy: reach.some((edges) => edges.some((x) => x.dst === e.dst && x.lossy)) }));
  if (targets.length === 0) return { kind: "conflict", formats: srcs };
  if (targets.length === 1) return { kind: "auto", target: targets[0] };
  return { kind: "choose", targets };
}

/** Targets the server named in 422 TARGET_REQUIRED, with lossiness looked up in `converts`. */
export function targetsFromServer(ids: unknown, converts: Edge[]): Target[] {
  if (!Array.isArray(ids)) return [];
  return ids
    .filter((id): id is string => typeof id === "string" && /^[a-z0-9.-]{1,32}$/.test(id))
    .map((id) => ({ id, lossy: converts.some((e) => e.dst === id && e.lossy) }));
}
