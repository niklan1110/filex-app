import { describe, expect, it } from "vitest";
import type { Format, ToolSummary } from "./api/client";
import { acceptsInWords, convertsHint, formatIndex, planTarget, searchTools, sourceFormat, targetsFromServer } from "./catalog";

const merge: ToolSummary = {
  id: "pdf.merge",
  title: "Merge PDFs",
  summary: "Combine several PDFs into one, in the order you add them.",
  category: "organize",
  group: "core",
  inputs: ["pdf"],
  outputs: ["pdf"],
  available: true,
  profile: "core",
  keywords: ["combine", "join", "append"],
};
const office: ToolSummary = {
  id: "office.to-pdf",
  title: "Office to PDF",
  summary: "Word, Excel and PowerPoint.",
  category: "convert",
  group: "office",
  inputs: ["docx", "xlsx"],
  outputs: ["pdf"],
  available: false,
  profile: "office",
  keywords: [],
};
const formats = formatIndex([
  { id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" },
  { id: "docx", title: "Word document", extensions: [".docx"], mime: "x" },
  { id: "xlsx", title: "Excel workbook", extensions: [".xlsx"], mime: "x" },
] satisfies Format[]);
const tools = [merge, office];

describe("searchTools", () => {
  it.each(["combine", "join", "pdf", "MERGE", "organize", " merge  pdfs "])("finds pdf.merge for %j", (q) => {
    expect(searchTools(tools, q, formats)).toContain(merge);
  });

  it("matches formats and 'X to Y' phrases", () => {
    expect(searchTools(tools, "docx", formats)).toEqual([office]);
    expect(searchTools(tools, "word to pdf", formats)).toEqual([office]);
  });

  it("keeps unavailable tools findable and returns all for an empty query", () => {
    expect(searchTools(tools, "excel", formats)).toEqual([office]);
    expect(searchTools(tools, "  ", formats)).toEqual(tools);
    expect(searchTools(tools, "zzz", formats)).toEqual([]);
  });
});

describe("acceptsInWords", () => {
  it("describes formats and file counts", () => {
    expect(acceptsInWords(["pdf"], { min: 2, max: 50, each: false }, formats)).toBe("PDF only, 2 to 50 files");
    expect(acceptsInWords(["docx", "xlsx"], { min: 1, max: 1, each: false }, formats)).toBe(
      "Word document or Excel workbook, one file",
    );
    expect(acceptsInWords(["epub"], { min: 1, max: 50, each: true }, formats)).toBe(
      "EPUB only, many files at once",
    );
  });

  it("says when a tool takes no files", () => {
    expect(acceptsInWords([], { min: 0, max: 0, each: false }, formats)).toBe("No files needed");
  });
});

describe("planTarget", () => {
  const e = (src: string, dst: string, lossy = false) => ({ src, dst, lossy });
  const fx = formatIndex([
    { id: "docx", title: "Word", extensions: [".docx"], mime: "a" },
    { id: "odt", title: "ODT", extensions: [".odt"], mime: "b" },
    { id: "xlsx", title: "Excel", extensions: [".xlsx"], mime: "c" },
    { id: "jpg", title: "JPEG", extensions: [".jpg", ".jpeg"], mime: "d" },
  ]);
  const office = { targetOption: null, converts: [e("docx", "odt"), e("docx", "txt", true), e("odt", "txt"), e("odt", "docx"), e("xlsx", "csv")] };

  it("is none for non-converters and for tools with a targetOption", () => {
    expect(planTarget({ converts: [], targetOption: null }, ["a.pdf"], fx)).toEqual({ kind: "none" });
    expect(planTarget({ converts: [e("pdf", "png")], targetOption: "format" }, ["a.pdf"], fx)).toEqual({ kind: "none" });
  });

  it("waits for files, then intersects reachable formats and keeps lossiness", () => {
    expect(planTarget(office, [], fx)).toEqual({ kind: "waiting" });
    expect(planTarget(office, ["A.DOCX"], fx)).toEqual({
      kind: "choose",
      targets: [{ id: "odt", lossy: false }, { id: "txt", lossy: true }],
    });
    // txt is lossy from docx, lossless from odt: any lossy edge marks it.
    expect(planTarget(office, ["a.docx", "b.odt"], fx)).toEqual({ kind: "auto", target: { id: "txt", lossy: true } });
  });

  it("reports the clashing source formats when nothing is common", () => {
    expect(planTarget(office, ["a.docx", "b.xlsx", "c.docx"], fx)).toEqual({ kind: "conflict", formats: ["docx", "xlsx"] });
  });

  it("matches any of a format's extensions and ignores unknown ones", () => {
    expect(sourceFormat("x.JPEG", ["jpg"], fx)).toBe("jpg");
    expect(sourceFormat("x.bin", ["jpg"], fx)).toBeNull();
    expect(sourceFormat("x.pdf", ["pdf"], formatIndex([]))).toBe("pdf"); // no format list: .id
  });

  it("keeps only well-formed server targets", () => {
    expect(targetsFromServer(["odt", 3, "<b>"], office.converts)).toEqual([{ id: "odt", lossy: false }]);
    expect(targetsFromServer(undefined, [])).toEqual([]);
  });
});

describe("convertsHint", () => {
  it("summarises converters and skips other tools", () => {
    expect(convertsHint({ category: "convert", inputs: ["docx", "xlsx", "pptx", "odt"], outputs: ["pdf"] })).toEqual({
      from: "DOCX, XLSX, PPTX +1",
      to: "PDF",
    });
    expect(convertsHint({ category: "organize", inputs: ["pdf"], outputs: ["pdf"] })).toBeNull();
  });
});
