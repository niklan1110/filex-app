import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToolDetail } from "../api/client";
import { formatIndex } from "../catalog";
import { RunTool } from "./RunTool";

const open = vi.hoisted(() => vi.fn());
vi.mock("../preview/renderPage", () => ({
  MAX_PREVIEW_BYTES: 50 * 1024 * 1024,
  renderFirstPage: vi.fn(),
  openPdf: open,
  PdfPasswordError: class extends Error {},
}));

// The organize tool as the API describes it (docs/api/openapi.json): one list option edited by
// the page grid, which the generic form skips.
const ORGANIZE: ToolDetail = {
  id: "pdf.organize",
  title: "Organize PDF pages",
  summary: "Reorder, turn, delete and duplicate pages, all in one go.",
  category: "organize",
  group: "core",
  inputs: ["pdf"],
  outputs: ["pdf"],
  available: true,
  profile: "core",
  keywords: [],
  files: { min: 1, max: 1, each: false, aux: {} },
  optionsSchema: {
    $defs: { PageStep: { type: "object", properties: { page: { type: "integer" }, rotate: { enum: [0, 90, 180, 270] } } } },
    properties: {
      pages: { type: "array", title: "Pages in their new order", items: { $ref: "#/$defs/PageStep" }, maxItems: 2000, "x-widget": "page-grid" },
    },
    required: ["pages"],
  },
  warnings: [],
  errors: [],
  converts: [],
  targetOption: null,
};
const FORMATS = formatIndex([{ id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" }]);

class FakeXHR {
  static last: FakeXHR | null = null;
  upload = { onprogress: null };
  body: FormData | null = null;
  open() {}
  setRequestHeader() {}
  abort() {}
  send(body: FormData) {
    this.body = body;
    FakeXHR.last = this;
  }
}

async function pickPdf() {
  fireEvent.change(document.getElementById("choose-files")!, {
    target: { files: [new File(["%PDF-1.7"], "deck.pdf", { type: "application/pdf" })] },
  });
  await screen.findByRole("listbox");
}

beforeEach(() => {
  FakeXHR.last = null;
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:t"), revokeObjectURL: vi.fn() }));
  open.mockResolvedValue({ numPages: 3, render: vi.fn(async () => new Blob(["j"])), destroy: vi.fn(async () => {}) });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("<RunTool> with the page grid", () => {
  it("has no generic field for the plan and sends the grid's plan as the pages option", async () => {
    render(<RunTool tool={ORGANIZE} formats={FORMATS} />);
    expect(screen.queryByText("This tool has no options. It runs as it is.")).toBeNull();
    await pickPdf();
    const tiles = screen.getAllByRole("option");
    fireEvent.click(tiles[0]);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(tiles[0]); // deselect 1
    fireEvent.click(tiles[2]);
    fireEvent.click(screen.getByRole("button", { name: "Turn right" }));
    fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
    expect(screen.getByText("Arrange the pages in the grid. The result will have 2 pages.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Organize PDF pages/ }));
    const sent = JSON.parse(String(FakeXHR.last!.body!.get("options")));
    expect(sent).toEqual({ pages: [{ page: 3, rotate: 90 }, { page: 2, rotate: 0 }] });
  });

  it("refuses to start with every page deleted, pointing at the grid", async () => {
    render(<RunTool tool={ORGANIZE} formats={FORMATS} />);
    await pickPdf();
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: /Organize PDF pages/ }));
    expect(FakeXHR.last).toBeNull();
    expect(screen.getByText("Keep at least one page: every page is marked deleted.")).toBeTruthy();
    expect(document.activeElement?.getAttribute("role")).toBe("option");
  });
});
