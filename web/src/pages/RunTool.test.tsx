import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browser, type Job, type ToolDetail } from "../api/client";
import { formatIndex } from "../catalog";
import { LimitsContext } from "../limits";
import { RunTool } from "./RunTool";

const MERGE: ToolDetail = {
  id: "pdf.merge",
  title: "Merge PDFs",
  summary: "Combine several PDFs into one.",
  category: "organize",
  group: "core",
  inputs: ["pdf"],
  outputs: ["pdf"],
  available: true,
  profile: "core",
  keywords: [],
  files: { min: 2, max: 50, each: false, aux: {} },
  optionsSchema: { properties: { bookmarks: { type: "boolean", title: "Add a bookmark for each file", default: false } } },
  warnings: [],
  errors: [],
  converts: [],
  targetOption: null,
};
const FORMATS = formatIndex([{ id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" }]);
const ID = "j_AAAAAAAAAAAAAAAAAAAAAA";
const job = (over: Partial<Job>): Job => ({
  id: ID, tool: "pdf.merge", status: "QUEUED", progress: 0, createdAt: null, finishedAt: null,
  expiresAt: null, inputs: [], steps: [], warnings: [], outputs: [], error: null, ...over,
});

class FakeXHR {
  static last: FakeXHR;
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  status = 0;
  response: unknown = null;
  responseType = "";
  body: FormData | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open() {}
  setRequestHeader() {}
  abort() { this.onabort?.(); }
  send(body: FormData) { this.body = body; FakeXHR.last = this; }
}

function pdf(name: string) {
  return new File(["%PDF-1.7"], name, { type: "application/pdf" });
}

function choose(files: File[]) {
  fireEvent.change(document.getElementById("choose-files")!, { target: { files } });
}

beforeEach(() => vi.stubGlobal("XMLHttpRequest", FakeXHR));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("<RunTool>", () => {
  it("refuses a wrong file type before upload, naming the file", () => {
    render(<RunTool tool={MERGE} formats={FORMATS} />);
    choose([pdf("a.pdf"), new File(["x"], "notes.docx")]);
    expect(screen.getByRole("alert").textContent).toContain("notes.docx: Merge PDFs takes PDF files only.");
    expect(screen.getAllByRole("listitem").filter((li) => li.closest(".files"))).toHaveLength(1);
  });

  it("takes the size limit from GET /config", () => {
    render(
      <LimitsContext.Provider value={{ maxUploadMb: 5, maxFiles: 3, retentionMin: 60 }}>
        <RunTool tool={MERGE} formats={FORMATS} />
      </LimitsContext.Provider>,
    );
    expect(screen.getByText(/up to 3 files, 5\.0 MB per file\./)).toBeTruthy();
    choose([new File([new Uint8Array(6 * 1024 * 1024)], "big.pdf")]);
    expect(screen.getByRole("alert").textContent).toContain("the limit is 5.0 MB per file");
  });

  it("reorders with the keyboard buttons and asks for more files when short", () => {
    render(<RunTool tool={MERGE} formats={FORMATS} />);
    choose([pdf("a.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /Merge PDFs/ }));
    expect(screen.getByRole("alert").textContent).toContain("Add at least 2 files first.");
    choose([pdf("b.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: "Move b.pdf up" }));
    const names = [...document.querySelectorAll(".files .fname")].map((n) => n.textContent);
    expect(names).toEqual(["b.pdf", "a.pdf"]);
  });

  it("uploads files in order with camelCase options, polls with ETag and offers the download", async () => {
    vi.useFakeTimers();
    const DL = `/api/v1/dl/${ID}/result?exp=1791025200&sig=AbC-_d`;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(url).toBe(`/api/v1/jobs/${ID}/result/link`);
        return new Response(JSON.stringify({ url: DL, expiresAt: "2026-10-10T12:05:00Z" }), { status: 200 });
      }
      const sent = (init?.headers as Record<string, string>)["If-None-Match"];
      if (sent === '"e1"') return new Response(null, { status: 304 });
      const body = job({
        status: "SUCCEEDED", progress: 1, expiresAt: new Date(Date.now() + 23 * 3600e3).toISOString(),
        outputs: [{ index: 0, name: "a.pdf", format: "pdf", size: 2048, role: "RESULT" }],
      });
      return new Response(JSON.stringify(body), { status: 200, headers: { ETag: '"e1"' } });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RunTool tool={MERGE} formats={FORMATS} />);
    choose([pdf("a.pdf"), pdf("b.pdf")]);
    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: /Merge PDFs/ }));

    const xhr = FakeXHR.last;
    expect(xhr.body!.getAll("files").map((f) => (f as File).name)).toEqual(["a.pdf", "b.pdf"]);
    expect(xhr.body!.get("options")).toBe('{"bookmarks":true}');
    expect(xhr.body!.has("target")).toBe(false); // never sent to a non-converter
    await act(async () => {
      xhr.upload.onprogress?.({ lengthComputable: true, loaded: 8, total: 16 } as ProgressEvent);
    });
    expect(screen.getByText("Uploading 100%")).toBeTruthy(); // first file fully sent
    await act(async () => {
      xhr.status = 202;
      xhr.response = job({});
      xhr.onload?.();
    });
    expect(screen.getByText("Waiting for a free worker.")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText(/Expires in 23 h/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "a.pdf is ready" })).toBe(document.activeElement);

    // Download mints a fresh link on every click and hands the /dl/ URL to the browser.
    const open = vi.spyOn(browser, "open").mockImplementation(() => {});
    const button = screen.getByRole("button", { name: /Download \(2\.0 KB\)/ });
    await act(async () => {
      fireEvent.click(button);
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      fireEvent.click(button);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(open.mock.calls).toEqual([[DL], [DL]]);
    expect(fetchMock.mock.calls.filter(([, i]) => i?.method === "POST")).toHaveLength(2);
    open.mockRestore();
  });

  describe("converter target", () => {
    const edge = (src: string, dst: string, lossy = false) => ({ src, dst, lossy });
    const CONVERT: ToolDetail = {
      ...MERGE,
      id: "office.convert",
      title: "Convert Office files",
      category: "convert",
      inputs: ["docx", "odt", "xlsx"],
      outputs: ["docx", "odt", "rtf", "txt", "xlsx", "ods", "csv"],
      files: { min: 1, max: 50, each: true, aux: {} },
      optionsSchema: {},
      converts: [
        edge("docx", "odt"), edge("docx", "rtf"), edge("docx", "txt", true),
        edge("odt", "docx"), edge("odt", "rtf"), edge("odt", "txt", true),
        edge("xlsx", "ods"), edge("xlsx", "csv", true),
      ],
    };
    const OFFICE_FORMATS = formatIndex(
      ["docx", "odt", "xlsx", "rtf", "txt", "ods", "csv"].map((id) => ({ id, title: id.toUpperCase(), extensions: [`.${id}`], mime: "x/y" })),
    );
    const file = (name: string) => new File(["x"], name);
    const run = () => screen.getByRole("button", { name: /Convert Office files/ }) as HTMLButtonElement;

    it("offers only reachable targets, marks lossy ones, gates Run and sends target", () => {
      render(<RunTool tool={CONVERT} formats={OFFICE_FORMATS} />);
      choose([file("a.docx")]);
      const group = screen.getByRole("group", { name: "Convert to" });
      const radios = [...group.querySelectorAll("input[type=radio]")].map((r) => (r as HTMLInputElement).value);
      expect(radios).toEqual(["odt", "rtf", "txt"]);
      expect(group.textContent).toContain("Some formatting may be lost.");
      expect(run().disabled).toBe(true);

      choose([file("b.odt")]); // docx ∩ odt → rtf, txt
      const left = [...screen.getByRole("group", { name: "Convert to" }).querySelectorAll("input")].map((r) => r.value);
      expect(left).toEqual(["rtf", "txt"]);
      fireEvent.click(screen.getByRole("radio", { name: /RTF/ }));
      expect(run().disabled).toBe(false);
      fireEvent.click(run());
      expect(FakeXHR.last.body!.get("target")).toBe("rtf");
    });

    it("names the conflict when the files share no target", () => {
      render(<RunTool tool={CONVERT} formats={OFFICE_FORMATS} />);
      choose([file("a.docx"), file("b.xlsx")]);
      expect(screen.getByRole("alert").textContent).toContain("DOCX and XLSX files cannot be converted to the same format");
      expect(run().disabled).toBe(true);
    });

    it("sends no target when there is one choice or the tool has a targetOption", () => {
      const toPdf = { ...CONVERT, title: "Office to PDF", converts: [edge("docx", "pdf"), edge("xlsx", "pdf")] };
      render(<RunTool tool={toPdf} formats={OFFICE_FORMATS} />);
      choose([file("a.docx"), file("b.xlsx")]);
      expect(screen.getByText(/Converts to/).textContent).toBe("Converts to PDF.");
      fireEvent.click(screen.getByRole("button", { name: /Office to PDF/ }));
      expect(FakeXHR.last.body!.has("target")).toBe(false);
      cleanup();

      const toImage = { ...CONVERT, title: "PDF to images", targetOption: "format", converts: [edge("docx", "png"), edge("docx", "jpg")] };
      render(<RunTool tool={toImage} formats={OFFICE_FORMATS} />);
      choose([file("a.docx")]);
      expect(screen.queryByRole("group", { name: "Convert to" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: /PDF to images/ }));
      expect(FakeXHR.last.body!.has("target")).toBe(false);
    });

    it("asks for a target on 422 TARGET_REQUIRED using details.targets", async () => {
      // The client sees one target for .docx; the server sniffed the content and knows two.
      const tool = { ...CONVERT, title: "Office to PDF", converts: [edge("docx", "pdf"), edge("doc", "odt", true)] };
      render(<RunTool tool={tool} formats={OFFICE_FORMATS} />);
      choose([file("a.docx")]);
      const runPdf = () => screen.getByRole("button", { name: /Office to PDF/ }) as HTMLButtonElement;
      fireEvent.click(runPdf());
      await act(async () => {
        const xhr = FakeXHR.last;
        xhr.status = 422;
        xhr.response = { error: { code: "TARGET_REQUIRED", message: "Pick one.", details: { targets: ["pdf", "odt"] } } };
        xhr.onload?.();
      });
      expect(screen.getByRole("alert").textContent).toContain("Choose a format to convert to.");
      const group = screen.getByRole("group", { name: "Convert to" });
      expect([...group.querySelectorAll("input")].map((r) => r.value)).toEqual(["pdf", "odt"]);
      expect(group.textContent).toContain("Some formatting may be lost.");
      expect(runPdf().disabled).toBe(true);
      fireEvent.click(screen.getByRole("radio", { name: /ODT/ }));
      fireEvent.click(runPdf());
      expect(FakeXHR.last.body!.get("target")).toBe("odt");
    });
  });
});
