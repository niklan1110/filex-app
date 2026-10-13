import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

const TOOLS = [
  {
    id: "pdf.merge",
    title: "Merge PDFs",
    summary: "Combine several PDFs into one, in the order you add them.",
    category: "organize",
    group: "core",
    inputs: ["pdf"],
    outputs: ["pdf"],
    available: true,
    profile: "core",
  },
  {
    id: "pdf.ocr",
    title: "Make searchable (OCR)",
    summary: "Add a text layer to scans.",
    category: "ocr",
    group: "ocr",
    inputs: ["pdf"],
    outputs: ["pdf"],
    available: false,
    profile: "ocr",
  },
];
const FORMATS = [{ id: "pdf", title: "PDF", extensions: [".pdf"], mime: "application/pdf" }];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  window.history.pushState(null, "", "/");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/v1/tools") return json(TOOLS);
      if (url === "/api/v1/formats") return json(FORMATS);
      return json({ error: { code: "NOT_FOUND", message: "Not Found", details: {} } }, 404);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("catalog", () => {
  it("lists tools by category, only from the FileX origin", async () => {
    render(<App />);
    const link = await screen.findByRole("link", { name: /Merge PDFs/ });
    expect(link.getAttribute("href")).toBe("/tools/pdf.merge");
    expect(screen.getByRole("heading", { level: 2, name: "Organize" })).toBeTruthy();
    const urls = vi.mocked(fetch).mock.calls.map(([u]) => String(u));
    expect(urls.every((u) => u.startsWith("/api/v1/"))).toBe(true);
    expect(screen.queryByRole("note")).toBeNull(); // no /config yet: no open-mode banner
  });

  it("search finds pdf.merge by 'combine' and 'pdf', and says when nothing matches", async () => {
    render(<App />);
    const box = await screen.findByRole("searchbox", { name: "Search tools" });
    await screen.findByText("Merge PDFs");

    fireEvent.change(box, { target: { value: "combine" } });
    expect(screen.getByText("Merge PDFs")).toBeTruthy();
    expect(screen.queryByText("Make searchable (OCR)")).toBeNull();
    expect(screen.getByText("1 tool found.")).toBeTruthy();

    fireEvent.change(box, { target: { value: "pdf" } });
    expect(screen.getByText("Merge PDFs")).toBeTruthy();
    expect(screen.getByText("2 tools found.")).toBeTruthy();

    fireEvent.change(box, { target: { value: "zzz" } });
    expect(screen.getByText("No tools match “zzz”")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByText("Merge PDFs")).toBeTruthy();
  });

  it("greys out unavailable tools with the profile hint and keeps them as links", async () => {
    render(<App />);
    const tile = await screen.findByRole("link", { name: /Make searchable/ });
    expect(tile.className).toContain("off");
    expect(tile.getAttribute("aria-disabled")).toBeNull();
    const hint = document.getElementById(tile.getAttribute("aria-describedby") ?? "");
    expect(hint?.textContent).toBe("Enable the ocr profile");
  });

  it("filters by category chip", async () => {
    render(<App />);
    await screen.findByText("Merge PDFs");
    const chip = screen.getByRole("button", { name: "OCR & scans" });
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("Merge PDFs")).toBeNull();
    expect(screen.getByText("Make searchable (OCR)")).toBeTruthy();
  });

  it("'/' focuses search, but not while typing in a field", async () => {
    render(<App />);
    const box = await screen.findByRole("searchbox");
    const themeRadio = screen.getByRole("radio", { name: "System" });
    themeRadio.focus();
    fireEvent.keyDown(themeRadio, { key: "/" });
    expect(document.activeElement).toBe(themeRadio); // an INPUT has focus: ignored
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(document.activeElement).toBe(box);
  });

  it("opens the tool page through the client router and shows API errors as an alert", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("link", { name: /Merge PDFs/ }));
    expect(window.location.pathname).toBe("/tools/pdf.merge");
    // /tools/pdf.merge is not mocked: the 404 envelope becomes a readable alert.
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(document.activeElement?.id).toBe("main"); // focus moved to the new page
    await act(async () => window.history.back());
  });

  it("lists popular conversions the server can do now and links them to the Convert page", async () => {
    const tools = [{ ...TOOLS[0], id: "office.to-pdf", title: "Office to PDF", category: "convert", inputs: ["docx"] }];
    vi.mocked(fetch).mockImplementation(async (url) => {
      if (url === "/api/v1/tools") return json(tools);
      if (url === "/api/v1/formats") return json([...FORMATS, { id: "docx", title: "Word document", extensions: [".docx"], mime: "x" }]);
      if (url === "/api/v1/convert/targets") {
        return json([
          { src: "docx", targets: [{ format: "pdf", steps: 1, lossy: false, groups: ["office"] }] },
          { src: "md", targets: [{ format: "epub", steps: 1, lossy: false, groups: ["ebook"] }] },
        ]);
      }
      return json({ error: { code: "NOT_FOUND", message: "Not Found", details: {} } }, 404);
    });
    render(<App />);
    const word = await screen.findByRole("link", { name: /^Word\s*to\s*PDF$/ });
    expect(word.getAttribute("href")).toBe("/convert?from=docx&to=pdf");
    expect(screen.getByRole("link", { name: /^Markdown\s*to\s*EPUB$/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /^Excel\s*to\s*PDF$/ })).toBeNull(); // not reachable now
    expect(screen.getByRole("link", { name: "Any format" }).getAttribute("href")).toBe("/convert");

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "word to pdf" } });
    expect(screen.getByRole("link", { name: /^Word\s*to\s*PDF$/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /^Markdown\s*to\s*EPUB$/ })).toBeNull();

    fireEvent.click(screen.getByRole("link", { name: /^Word\s*to\s*PDF$/ }));
    expect(window.location.pathname + window.location.search).toBe("/convert?from=docx&to=pdf");
    await act(async () => window.history.back());
  });
});
