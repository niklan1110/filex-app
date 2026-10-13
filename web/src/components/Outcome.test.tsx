import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api/client";
import { JobView } from "./JobView";
import { OutcomeBlock, outcomeOf } from "./Outcome";

// PDF.js never loads in these tests: the renderer and the output fetch are mocked.
const render1 = vi.hoisted(() => vi.fn());
const fetchOut = vi.hoisted(() => vi.fn());
vi.mock("../preview/renderPage", () => ({ MAX_PREVIEW_BYTES: 50 * 1024 * 1024, renderFirstPage: render1 }));
vi.mock("../api/client", async (orig) => ({ ...(await orig<typeof import("../api/client")>()), fetchOutput: fetchOut }));

const MB = 1024 * 1024;
const ID = "j_AAAAAAAAAAAAAAAAAAAAAA";
const done = (over: Partial<Job> = {}): Job => ({
  id: ID, tool: "pdf.compress", status: "SUCCEEDED", progress: 1, createdAt: null, finishedAt: null, expiresAt: null,
  inputs: [{ name: "report.pdf", format: "pdf", size: 8.4 * MB }],
  steps: [], warnings: [],
  outputs: [{ index: 0, name: "report.pdf", format: "pdf", size: 3.2 * MB, role: "RESULT" }],
  error: null, ...over,
});
const warn = (code: string, message: string) => ({ code, message, step: 0 });

function sizesLabel() {
  return screen.getByRole("img", { name: /^Original/ }).getAttribute("aria-label");
}

beforeEach(() => {
  render1.mockReset();
  fetchOut.mockReset();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("outcome", () => {
  it("shows the poster percent, the figure and a full sentence for screen readers", () => {
    render(<OutcomeBlock outcome={outcomeOf(done())!} />);
    expect(screen.getByText("−62%")).toBeTruthy();
    expect(screen.getByText("8.4 MB → 3.2 MB")).toBeTruthy();
    expect(screen.getByText("5.2 MB saved")).toBeTruthy();
    expect(sizesLabel()).toBe("Original 8.4 MB, compressed 3.2 MB, 62 percent smaller.");
  });

  it("says Target reached when targetMb was set and no warning came back", () => {
    render(<JobView initial={done()} toolTitle="Compress PDF" onStartOver={() => {}} options={{ targetMb: 4 }} />);
    expect(screen.getByText("Target reached")).toBeTruthy();
    expect(screen.getByText("5.2 MB saved, under the 4 MB target")).toBeTruthy();
    expect(sizesLabel()).toContain("target under 4 MB reached");
  });

  it("says Best we could do on TARGET_NOT_REACHED, with the target from the options", () => {
    const job = done({ outputs: [{ index: 0, name: "r.pdf", format: "pdf", size: 2.4 * MB, role: "RESULT" }], warnings: [warn("TARGET_NOT_REACHED", "x")] });
    render(<JobView initial={job} toolTitle="Compress PDF" onStartOver={() => {}} options={{ targetMb: 2 }} />);
    expect(screen.getByText("Best we could do")).toBeTruthy();
    expect(screen.getByText("Best we could do: 2.4 MB (target 2 MB).")).toBeTruthy();
    expect(sizesLabel()).toContain("target 2 MB not reached");
  });

  it("reads the target from the warning text, or falls back to the message", () => {
    const parsed = outcomeOf(done({ warnings: [warn("TARGET_NOT_REACHED", "Reached 2.4 MB; the target of 2 MB was not reachable.")] }));
    expect(parsed?.target).toEqual({ reached: false, label: "2 MB", note: null });
    const odd = outcomeOf(done({ warnings: [warn("TARGET_NOT_REACHED", "Could not get there.")] }));
    expect(odd?.target).toEqual({ reached: false, label: null, note: "Could not get there." });
    render(<OutcomeBlock outcome={odd!} />);
    expect(screen.getByText("Best we could do: 3.2 MB. Could not get there.")).toBeTruthy();
  });

  it("never shows a minus when the file was not made smaller (NOT_SMALLER)", () => {
    const job = done({
      outputs: [{ index: 0, name: "r.pdf", format: "pdf", size: 8.4 * MB, role: "RESULT" }],
      warnings: [warn("NOT_SMALLER", "report.pdf is already compact")],
    });
    const { container } = render(<OutcomeBlock outcome={outcomeOf(job)!} />);
    expect(screen.getByText("Already as small as it gets")).toBeTruthy();
    expect(container.textContent).not.toContain("−");
    expect(sizesLabel()).toBe("Original 8.4 MB, now 8.4 MB, not smaller.");
  });

  it("totals a batch across all files", () => {
    const job = done({
      inputs: [
        { name: "a.pdf", format: "pdf", size: 6 * MB },
        { name: "b.pdf", format: "pdf", size: 4 * MB },
      ],
      outputs: [
        { index: 0, name: "a.pdf", format: "pdf", size: 2 * MB, role: "RESULT" },
        { index: 1, name: "b.pdf", format: "pdf", size: 2 * MB, role: "RESULT" },
        { index: 2, name: "log.txt", format: "txt", size: 50 * MB, role: "REPORT" },
      ],
    });
    render(<OutcomeBlock outcome={outcomeOf(job)!} />);
    expect(screen.getByText("−60%")).toBeTruthy();
    expect(screen.getByText("10 MB → 4.0 MB")).toBeTruthy();
    expect(screen.getByText("6.0 MB saved across 2 files")).toBeTruthy();
  });

  it("is only for size tools", () => {
    expect(outcomeOf(done({ tool: "pdf.merge" }))).toBeNull();
  });
});

describe("before/after preview", () => {
  const original = new File(["%PDF-1.7"], "report.pdf", { type: "application/pdf" });
  const view = (job = done()) => render(<JobView initial={job} toolTitle="Compress PDF" onStartOver={() => {}} files={[original]} />);

  it("renders page 1 of the original and of the result as images, with a labelled slider", async () => {
    render1.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    fetchOut.mockResolvedValue(new ArrayBuffer(8));
    view();
    await act(async () => {});
    expect(render1).toHaveBeenCalledTimes(2);
    expect(fetchOut).toHaveBeenCalledWith(ID, 0, expect.any(AbortSignal));
    expect(screen.getByAltText("Page 1 of report.pdf before compression, 8 B")).toBeTruthy();
    expect(screen.getByAltText("Page 1 of report.pdf after compression, 3.2 MB")).toBeTruthy();
    expect(document.querySelector("iframe, object, embed")).toBeNull();

    const slider = screen.getByRole("slider", { name: /Compare position/ });
    expect(slider.getAttribute("aria-valuetext")).toBe("50% before, 50% after");
    fireEvent.change(slider, { target: { value: "30" } });
    expect(slider.getAttribute("aria-valuetext")).toBe("30% before, 70% after");
    expect((slider.parentElement as HTMLElement).style.getPropertyValue("--pos")).toBe("30%");
  });

  it("falls back to a sentence when PDF.js fails, and keeps the result card", async () => {
    render1.mockRejectedValue(new Error("bad pdf"));
    view();
    await act(async () => {});
    expect(screen.getByText("The preview could not be drawn. The result itself is fine.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Download/ })).toBeTruthy();
  });

  it("skips files over the memory cap without loading anything", async () => {
    view(done({ outputs: [{ index: 0, name: "r.pdf", format: "pdf", size: 60 * MB, role: "RESULT" }] }));
    await act(async () => {});
    expect(render1).not.toHaveBeenCalled();
    expect(screen.getByText("No preview for files over 50 MB.")).toBeTruthy();
  });

  it("stops rendering when the card goes away", async () => {
    let signal: AbortSignal | undefined;
    render1.mockImplementation((_d: ArrayBuffer, s: AbortSignal) => {
      signal = s;
      return new Promise(() => {});
    });
    const { unmount } = view();
    await act(async () => {});
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("is not shown without the original file in the browser", () => {
    render(<JobView initial={done()} toolTitle="Compress PDF" onStartOver={() => {}} />);
    expect(screen.queryByRole("group", { name: "Page 1, before and after" })).toBeNull();
  });
});
