import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browser, type Job } from "../api/client";
import { MyJobs } from "./MyJobs";

const job = (id: string, over: Partial<Job>): Job => ({
  id, tool: "pdf.merge", status: "SUCCEEDED", progress: 1, createdAt: "2026-10-10T09:41:00Z", finishedAt: null,
  expiresAt: null, inputs: [{ name: "report.pdf", format: "pdf", size: 10 }], steps: [], warnings: [],
  outputs: [{ index: 0, name: "report.pdf", format: "pdf", size: 10, role: "RESULT" }], error: null, ...over,
});
const A = "j_AAAAAAAAAAAAAAAAAAAAAA";
const B = "j_BBBBBBBBBBBBBBBBBBBBBB";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/jobs?limit=20") return json({ items: [job(A, {})], nextBefore: 7 });
      if (url === "/api/v1/jobs?limit=20&before=7") return json({ items: [job(B, { status: "FAILED", outputs: [] })], nextBefore: null });
      if (url === "/api/v1/tools") return json([{ id: "pdf.merge", title: "Merge PDFs" }]);
      if (init?.method === "POST" && url === `/api/v1/jobs/${A}/result/link`) {
        return json({ url: `/api/v1/dl/${A}/result?exp=1&sig=x`, expiresAt: "2026-10-10T10:00:00Z" });
      }
      return json({ error: { code: "JOB_NOT_FOUND", message: "gone", details: {} } }, 404);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("<MyJobs>", () => {
  it("lists the caller's jobs, pages back, and downloads through a minted link", async () => {
    const open = vi.spyOn(browser, "open").mockImplementation(() => {});
    render(<MyJobs />);
    expect((await screen.findByRole("link", { name: "Merge PDFs" })).getAttribute("href")).toBe("/tools/pdf.merge");
    expect(screen.getByText("report.pdf")).toBeTruthy();

    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Download/ })));
    expect(open).toHaveBeenCalledWith(`/api/v1/dl/${A}/result?exp=1&sig=x`);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Show older jobs" })));
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show older jobs" })).toBeNull();
    expect(screen.getAllByRole("button", { name: /Download/ })).toHaveLength(1); // failed job: none
  });

  it("refuses a link that is not FileX's own /dl/ path", async () => {
    vi.mocked(fetch).mockImplementation(async (url) => {
      if (url === "/api/v1/jobs?limit=20") return json({ items: [job(A, {})], nextBefore: null });
      if (url === "/api/v1/tools") return json([]);
      return json({ url: "https://evil.example/x", expiresAt: "" });
    });
    const open = vi.spyOn(browser, "open").mockImplementation(() => {});
    render(<MyJobs />);
    const button = await screen.findByRole("button", { name: /Download/ });
    await act(async () => fireEvent.click(button));
    expect(open).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("download link FileX does not trust");
  });

  it("labels convert jobs by the format they make and links them to the Convert page", async () => {
    const running = job(B, {
      tool: "convert", status: "RUNNING", outputs: [],
      steps: [{ index: 0, tool: "pdf.to-text", group: "core", status: "RUNNING", progress: 0, message: "" }],
    });
    const done = job(A, {
      tool: "convert", outputs: [{ index: 0, name: "report.pdf", format: "pdf", size: 10, role: "RESULT" }],
    });
    vi.mocked(fetch).mockImplementation(async (url) => {
      if (url === "/api/v1/jobs?limit=20") return json({ items: [done, running], nextBefore: null });
      if (url === "/api/v1/tools") return json([{ id: "pdf.to-text", title: "PDF to text", outputs: ["txt"] }]);
      return json({}, 404);
    });
    render(<MyJobs />);
    const link = await screen.findByRole("link", { name: /^Convert\s*to\s*PDF$/ });
    expect(link.getAttribute("href")).toBe("/convert");
    expect(link.textContent).toBe("Convert →  to PDF");
    expect(await screen.findByRole("link", { name: /^Convert\s*to\s*TXT$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /result of Convert to PDF/ })).toBeTruthy();
  });
});
