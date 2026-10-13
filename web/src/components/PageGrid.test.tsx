import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageGrid } from "./PageGrid";
import { toPlan, type Entry } from "./pagePlan";

// PDF.js never loads here: openPdf is mocked with a fake document.
const open = vi.hoisted(() => vi.fn());
vi.mock("../preview/renderPage", () => ({
  openPdf: open,
  PdfPasswordError: class PdfPasswordError extends Error {},
}));

function fakeDoc(numPages: number) {
  return { numPages, render: vi.fn(async () => new Blob(["jpg"])), destroy: vi.fn(async () => {}) };
}

let plan: Entry[] | null = null;
function Harness({ max = 2000, error }: { max?: number; error?: string }) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [file] = useState(() => new File(["%PDF"], "doc.pdf"));
  return (
    <PageGrid
      file={file}
      entries={entries}
      onEntries={(e) => {
        plan = e;
        setEntries(e);
      }}
      maxPages={max}
      error={error}
    />
  );
}

async function mount(pages = 4, props: { max?: number; error?: string } = {}) {
  const doc = fakeDoc(pages);
  open.mockResolvedValue(doc);
  render(<Harness {...props} />);
  await screen.findByRole("listbox");
  return doc;
}
const tiles = () => screen.getAllByRole("option");
const order = () => toPlan(plan!).map((s) => (s.rotate ? `${s.page}@${s.rotate}` : String(s.page)));
const status = () => screen.getAllByRole("status").map((s) => s.textContent).join(" ");

beforeEach(() => {
  plan = null;
  open.mockReset();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:t"), revokeObjectURL: vi.fn() }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("<PageGrid>", () => {
  it("shows one labelled tile per page with a thumbnail image, never a frame", async () => {
    const doc = await mount(3);
    expect(tiles().map((t) => t.getAttribute("aria-label"))).toEqual([
      "Page 1, page 1 of the original",
      "Page 2, page 2 of the original",
      "Page 3, page 3 of the original",
    ]);
    expect(order()).toEqual(["1", "2", "3"]);
    await vi.waitFor(() => expect(document.querySelectorAll(".pg-frame img")).toHaveLength(3));
    expect(doc.render).toHaveBeenCalledWith(1, expect.any(Number), expect.any(AbortSignal), true);
    expect(document.querySelector("iframe, object, embed")).toBeNull();
  });

  it("reorders with the keyboard only: arrows focus, Space selects, Ctrl+arrow moves, focus stays", async () => {
    await mount(4);
    const first = tiles()[0];
    expect(first.tabIndex).toBe(0);
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(document.activeElement).toBe(tiles()[1]);
    fireEvent.keyDown(document.activeElement!, { key: " " });
    expect(tiles()[1].getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft", ctrlKey: true });
    expect(order()).toEqual(["2", "1", "3", "4"]);
    expect(status()).toContain("Page 2 of the original moved earlier, now page 1.");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Page 1, page 2 of the original");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft", altKey: true });
    expect(status()).toContain("Already at the start.");
  });

  it("turns, duplicates, deletes and restores the selection with the toolbar", async () => {
    await mount(3);
    fireEvent.click(tiles()[2]);
    fireEvent.click(screen.getByRole("button", { name: "Turn right" }));
    expect(order()).toEqual(["1", "2", "3@90"]);
    expect(tiles()[2].getAttribute("aria-label")).toBe("Page 3, page 3 of the original, turned 90° clockwise");
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(order()).toEqual(["1", "2", "3@90", "3@90"]);
    fireEvent.click(tiles()[2]); // deselect page 3, select page 1
    fireEvent.click(tiles()[0]);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(order()).toEqual(["2", "3@90", "3@90"]);
    expect(tiles()[0].getAttribute("aria-label")).toBe("Page 1 of the original, deleted");
    expect(tiles()[0].textContent).toContain("Deleted"); // not colour alone
    expect(status()).toContain("Page 1 of the original deleted. 3 pages in the result.");
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(order()).toEqual(["1", "2", "3@90", "3@90"]);
  });

  it("keeps toolbar focus and explains when nothing is selected", async () => {
    await mount(2);
    const earlier = screen.getByRole("button", { name: "Earlier" });
    expect(earlier.getAttribute("aria-disabled")).toBe("true");
    act(() => earlier.focus());
    fireEvent.click(earlier);
    expect(document.activeElement).toBe(earlier);
    expect(status()).toContain("Select pages first.");
  });

  it("moves the selection to a page number, with shift-click selecting a range", async () => {
    await mount(5);
    fireEvent.click(tiles()[3]);
    fireEvent.click(tiles()[4], { shiftKey: true });
    const input = screen.getByLabelText("Move to page");
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(order()).toEqual(["4", "5", "1", "2", "3"]);
    fireEvent.change(input, { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    expect(status()).toContain("Enter a page number from 1 to 5.");
  });

  it("drags the selection next to another tile", async () => {
    await mount(3);
    fireEvent.dragStart(tiles()[2], { dataTransfer: { setData: () => {}, effectAllowed: "" } });
    fireEvent.dragOver(tiles()[0]); // left half (jsdom has no layout): drop before page 1
    expect(tiles()[0].dataset.mark).toBe("before");
    fireEvent.drop(tiles()[0]);
    expect(order()).toEqual(["3", "1", "2"]);
    expect(status()).toContain("Page 3 of the original moved, now page 1.");
  });

  it("refuses a duplicate past the page limit", async () => {
    await mount(2, { max: 2 });
    fireEvent.click(tiles()[0]);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(order()).toEqual(["1", "2"]);
    expect(status()).toContain("at most 2 pages");
  });

  it("ties a form error to the grid", async () => {
    await mount(2, { error: "Keep at least one page." });
    const list = screen.getByRole("listbox");
    expect(list.getAttribute("aria-invalid")).toBe("true");
    expect(list.getAttribute("aria-describedby")).toContain("pg-err");
  });

  it("says why a password-protected file cannot be arranged", async () => {
    const { PdfPasswordError } = await import("../preview/renderPage");
    open.mockRejectedValue(new PdfPasswordError("locked"));
    render(<Harness />);
    expect((await screen.findByRole("alert")).textContent).toContain("password-protected");
    expect(plan).toBeNull();
  });

  it("frees the document when unmounted", async () => {
    const doc = await mount(2);
    cleanup();
    expect(doc.destroy).toHaveBeenCalled();
  });
});
