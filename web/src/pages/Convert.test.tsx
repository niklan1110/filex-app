import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConvertPlan, ConvertSource, Job } from "../api/client";
import { formatIndex } from "../catalog";
import { ConvertPage } from "./Convert";

const FORMATS = formatIndex([
  { id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" },
  { id: "docx", title: "Word document", extensions: [".docx"], mime: "x" },
  { id: "txt", title: "Plain text", extensions: [".txt"], mime: "text/plain" },
  { id: "epub", title: "EPUB ebook", extensions: [".epub"], mime: "application/epub+zip" },
  { id: "md", title: "Markdown", extensions: [".md"], mime: "text/markdown" },
  { id: "png", title: "PNG image", extensions: [".png"], mime: "image/png" },
]);
const TARGETS: ConvertSource[] = [
  {
    src: "docx",
    targets: [
      { format: "pdf", steps: 1, lossy: false, groups: ["office"] },
      { format: "txt", steps: 2, lossy: true, groups: ["core", "office"] },
    ],
  },
  { src: "md", targets: [{ format: "epub", steps: 1, lossy: false, groups: ["ebook"] }] },
  { src: "png", targets: [{ format: "pdf", steps: 1, lossy: false, groups: ["core"] }] },
];
const DOCX_PDF: ConvertPlan = {
  sources: ["docx"], target: "pdf", cost: 1, lossy: false, warnings: [],
  steps: [{ index: 0, tool: "office.to-pdf", title: "Office to PDF", group: "office", src: ["docx"], dst: "pdf", lossy: false }],
};
const DOCX_TXT: ConvertPlan = {
  sources: ["docx"], target: "txt", cost: 7, lossy: true,
  warnings: [{ code: "LOSSY_STEP", message: "Step 2 may lose formatting.", step: 1 }],
  steps: [
    { index: 0, tool: "office.to-pdf", title: "Office to PDF", group: "office", src: ["docx"], dst: "pdf", lossy: false },
    { index: 1, tool: "pdf.to-text", title: "PDF to text", group: "core", src: ["pdf"], dst: "txt", lossy: true },
  ],
};
const ID = "j_AAAAAAAAAAAAAAAAAAAAAA";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const err = (code: string, message: string, details = {}) => ({ error: { code, message, details } });

let answers: Record<string, () => Response>;

class FakeXHR {
  static last: FakeXHR;
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  url = "";
  status = 0;
  response: unknown = null;
  responseType = "";
  body: FormData | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open(_method: string, url: string) { this.url = url; }
  setRequestHeader() {}
  abort() { this.onabort?.(); }
  send(body: FormData) { this.body = body; FakeXHR.last = this; }
}

beforeEach(() => {
  window.history.pushState(null, "", "/convert");
  answers = {
    "/api/v1/convert/targets": () => json(TARGETS),
    "/api/v1/convert/plan?from=docx&to=pdf&files=1": () => json(DOCX_PDF),
    "/api/v1/convert/plan?from=docx&to=txt&files=1": () => json(DOCX_TXT),
  };
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => answers[url]?.() ?? json(err("NOT_FOUND", "Not Found"), 404)),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function choose(files: File[]) {
  fireEvent.change(document.getElementById("choose-files")!, { target: { files } });
}
const file = (name: string) => new File(["x"], name);

describe("<ConvertPage>", () => {
  it("detects the format, lists reachable targets by family, previews the route and runs the job", async () => {
    render(<ConvertPage formats={FORMATS} />);
    await screen.findByText("Drop files anywhere on this page");
    choose([file("report.docx")]);

    expect(screen.getByText("Word document")).toBeTruthy();
    const pdf = screen.getByRole("radio", { name: /^PDF/ });
    expect(screen.getByRole("group", { name: "PDF" })).toBeTruthy();
    expect(within(screen.getByRole("group", { name: "Documents" })).getByRole("radio", { name: /TXT.*2 steps.*May lose formatting/ })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /EPUB/ })).toBeNull(); // not reachable from DOCX

    await act(async () => fireEvent.click(pdf));
    expect(await screen.findByText("Office to PDF")).toBeTruthy();
    expect(screen.getByText("office add-on")).toBeTruthy();
    expect(screen.getByText(/Step 1: Word document to PDF with Office to PDF, on the office add-on\./)).toBeTruthy();

    const run = screen.getByRole("button", { name: /Convert 1 file to PDF/ });
    await act(async () => fireEvent.click(run));
    expect(FakeXHR.last.url).toBe("/api/v1/convert/jobs");
    expect(FakeXHR.last.body?.get("target")).toBe("pdf");
    expect((FakeXHR.last.body?.get("files") as File).name).toBe("report.docx");
    expect(FakeXHR.last.body?.has("options")).toBe(false);

    const job: Job = {
      id: ID, tool: "convert", status: "QUEUED", progress: 0, createdAt: null, finishedAt: null, expiresAt: null,
      inputs: [{ name: "report.docx", format: "docx", size: 1 }], warnings: [], outputs: [], error: null,
      steps: [{ index: 0, tool: "office.to-pdf", group: "office", status: "QUEUED", progress: 0, message: "" }],
    };
    await act(async () => {
      FakeXHR.last.status = 202;
      FakeXHR.last.response = job;
      FakeXHR.last.onload?.();
    });
    expect(screen.getByRole("heading", { name: "Convert to PDF, 1 file" })).toBeTruthy();
    expect(screen.getByText("Step 1: DOCX to PDF (Office to PDF)")).toBeTruthy();
  });

  it("marks a lossy step with a dashed rail and words, and counts the steps", async () => {
    render(<ConvertPage formats={FORMATS} />);
    await screen.findByText("Drop files anywhere on this page");
    choose([file("report.docx")]);
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: /^TXT/ })));
    expect(await screen.findByText("Some formatting may be lost")).toBeTruthy();
    expect(screen.getByText(/2 steps · office add-on, core worker · 1 lossy step/)).toBeTruthy();
    expect(document.querySelectorAll(".rail li[data-lossy]")).toHaveLength(1);
    expect(screen.getByText("Route ready: 2 steps, some formatting may be lost.")).toBeTruthy();
  });

  it("explains NO_ROUTE with the add-on to enable, from a catalog link, and blocks the run", async () => {
    window.history.pushState(null, "", "/convert?from=md&to=pdf");
    answers["/api/v1/convert/plan?from=md&to=pdf&files=1"] = () =>
      json(err("NO_ROUTE", "FileX cannot convert Markdown to PDF.", { profiles: ["ebook", "office"] }), 422);
    render(<ConvertPage formats={FORMATS} />);
    const alert = await screen.findByText("No route from MD to PDF right now.");
    const box = alert.closest(".alert") as HTMLElement;
    expect(box.textContent).toContain("Enable the ebook and office add-ons");
    expect(within(box).getByText("ebook,office")).toBeTruthy();
    expect(within(box).getByText("COMPOSE_PROFILES")).toBeTruthy();
    expect((screen.getByLabelText("From") as HTMLSelectElement).value).toBe("md");
    expect((screen.getByRole("button", { name: /^Convert/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says when mixed files share no format, and when a route takes fewer files", async () => {
    render(<ConvertPage formats={FORMATS} />);
    await screen.findByText("Drop files anywhere on this page");
    choose([file("a.md"), file("b.png")]);
    expect(screen.getByText("These files have no format in common.")).toBeTruthy();
    expect(screen.getByText(/MD and PNG files cannot be converted to the same format/)).toBeTruthy();

    cleanup();
    answers["/api/v1/convert/plan?from=docx&to=pdf&files=3"] = () =>
      json(err("TOO_MANY_FILES", "Convert at most 2 of these files at once.", { max: 2 }), 422);
    render(<ConvertPage formats={FORMATS} />);
    await screen.findByText("Drop files anywhere on this page");
    choose([file("a.docx"), file("b.docx"), file("c.docx")]);
    expect(screen.getByText(/· 3 files/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: /^PDF/ })));
    expect(await screen.findByText("Too many files for this route.")).toBeTruthy();
    expect(screen.getByText("One step takes at most 2 files. Remove 1 or convert them in separate jobs.")).toBeTruthy();
  });

  it("names the add-on when no running worker takes the dropped format", async () => {
    answers["/api/v1/tools"] = () =>
      json([
        { id: "ebook.convert", title: "Convert ebooks", summary: "", category: "ebook", group: "ebook", inputs: ["epub"], outputs: ["pdf"], available: false, profile: "ebook", keywords: [] },
      ]);
    render(<ConvertPage formats={FORMATS} />);
    await screen.findByText("Drop files anywhere on this page");
    choose([file("book.epub"), file("notes.xyz")]);
    expect(screen.getByRole("alert").textContent).toContain("notes.xyz: Convert takes documents, ebooks, images and PDFs only.");
    expect(await screen.findByText("Nothing on this server converts EPUB ebook files right now.")).toBeTruthy();
    expect(await screen.findByText(/Enable the ebook add-on/)).toBeTruthy();
  });

  it("shows a load error with Try again when the server has no planner", async () => {
    delete answers["/api/v1/convert/targets"];
    render(<ConvertPage formats={FORMATS} />);
    expect(await screen.findByText("Could not load the conversions.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
