import { describe, expect, it } from "vitest";
import type { ConvertSource, Job } from "./api/client";
import { formatIndex } from "./catalog";
import {
  byFamily,
  convertHref,
  convertTarget,
  detectSources,
  popularPairs,
  reachable,
  readConvertQuery,
  stepLabels,
  workerName,
} from "./convert";

const LIST: ConvertSource[] = [
  {
    src: "docx",
    targets: [
      { format: "pdf", steps: 1, lossy: false, groups: ["office"] },
      { format: "txt", steps: 2, lossy: true, groups: ["core", "office"] },
      { format: "epub", steps: 1, lossy: false, groups: ["ebook"] },
    ],
  },
  {
    src: "odt",
    targets: [
      { format: "pdf", steps: 1, lossy: false, groups: ["office"] },
      { format: "txt", steps: 1, lossy: false, groups: ["office"] },
    ],
  },
  { src: "png", targets: [{ format: "pdf", steps: 1, lossy: false, groups: ["core"] }] },
  { src: "md", targets: [{ format: "epub", steps: 1, lossy: false, groups: ["ebook"] }] },
];
const FORMATS = formatIndex([
  { id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" },
  { id: "docx", title: "Word document", extensions: [".docx"], mime: "x" },
  { id: "md", title: "Markdown", extensions: [".md", ".markdown"], mime: "text/markdown" },
  { id: "png", title: "PNG image", extensions: [".png"], mime: "image/png" },
]);

describe("convert helpers", () => {
  it("intersects the targets of mixed sources, keeping the worst case", () => {
    expect(reachable(LIST, ["docx"]).map((t) => t.format)).toEqual(["pdf", "txt", "epub"]);
    expect(reachable(LIST, ["docx", "odt"])).toEqual([
      { format: "pdf", steps: 1, lossy: false, groups: ["office"] },
      { format: "txt", steps: 2, lossy: true, groups: ["core", "office"] },
    ]);
    expect(reachable(LIST, ["docx", "md"]).map((t) => t.format)).toEqual(["epub"]);
    expect(reachable(LIST, ["png", "md"])).toEqual([]);
    expect(reachable(LIST, [])).toEqual([]);
    expect(reachable(undefined, ["docx"])).toEqual([]);
  });

  it("groups targets by family in a fixed order", () => {
    const g = byFamily([{ format: "png" }, { format: "epub" }, { format: "txt" }, { format: "pdf" }, { format: "zz" }]);
    expect(g.map((f) => f.title)).toEqual(["PDF", "Documents", "Ebooks & comics", "Images", "Other"]);
  });

  it("names workers as the core worker or an add-on", () => {
    expect(workerName("core")).toBe("core worker");
    expect(workerName("office")).toBe("office add-on");
  });

  it("detects formats from file names, longest extension wins, unknown kept apart", () => {
    expect(detectSources(["a.docx", "b.MD", "c.docx", "d.xyz", "notes.markdown"], FORMATS)).toEqual({
      ids: ["docx", "md"],
      unknown: ["d.xyz"],
    });
  });

  it("builds popular pairs only from reachable routes, in priority order", () => {
    expect(popularPairs(LIST)).toEqual([
      { from: "docx", to: "pdf", lossy: false, steps: 1 },
      { from: "png", to: "pdf", lossy: false, steps: 1 },
      { from: "md", to: "epub", lossy: false, steps: 1 },
    ]);
    expect(popularPairs(LIST, 1)).toHaveLength(1);
    expect(popularPairs(undefined)).toEqual([]);
  });

  it("reads and writes only format-like ids in the address", () => {
    expect(convertHref("docx", "pdf")).toBe("/convert?from=docx&to=pdf");
    expect(convertHref("../x", "pdf")).toBe("/convert?to=pdf");
    expect(convertHref()).toBe("/convert");
    expect(readConvertQuery("?from=md&to=EPUB")).toEqual({ from: "md", to: "" });
    expect(readConvertQuery("?from=%3Cscript%3E")).toEqual({ from: "", to: "" });
  });

  it("words each step for the job view", () => {
    expect(
      stepLabels([
        { index: 0, tool: "office.to-pdf", title: "Office to PDF", group: "office", src: ["docx"], dst: "pdf", lossy: false },
        { index: 1, tool: "pdf.to-text", title: "PDF to text", group: "core", src: ["pdf"], dst: "txt", lossy: true },
      ]),
    ).toEqual(["DOCX to PDF (Office to PDF)", "PDF to TXT (PDF to text)"]);
  });

  it("finds a convert job's target from its result, else its last step's tool", () => {
    const base: Job = {
      id: "j_AAAAAAAAAAAAAAAAAAAAAA", tool: "convert", status: "RUNNING", progress: 0.5, createdAt: null, finishedAt: null,
      expiresAt: null, inputs: [], warnings: [], outputs: [], error: null,
      steps: [{ index: 0, tool: "pdf.to-text", group: "core", status: "RUNNING", progress: 0, message: "" }],
    };
    const tools = [{ id: "pdf.to-text", outputs: ["txt"] }, { id: "office.convert", outputs: ["docx", "odt"] }];
    expect(convertTarget(base, tools)).toBe("txt");
    expect(convertTarget({ ...base, steps: [{ ...base.steps[0], tool: "office.convert" }] }, tools)).toBeNull();
    expect(
      convertTarget({ ...base, outputs: [{ index: 0, name: "a.epub", format: "epub", size: 1, role: "RESULT" }] }, tools),
    ).toBe("epub");
  });
});
