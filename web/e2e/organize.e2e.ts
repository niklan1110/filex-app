// pdf.organize with the page grid (plan tasks 21, 22) against the live stack: keyboard reorder,
// toolbar turn / duplicate / delete, drag, then the downloaded PDF is checked with pdf-lib for the
// page order and rotations. Also axe on the grid (light and dark) and 360 px reflow.
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";
import { PDFArray, PDFDocument, PDFRawStream, decodePDFRawStream, type PDFPage } from "pdf-lib";
import {
  JOB_TIMEOUT,
  corpus,
  deleteNow,
  download,
  expect,
  expectNoHorizontalScroll,
  openTool,
  requireStack,
  runButton,
  settle,
  shot,
  test,
  waitForResult,
} from "./helpers";

test.beforeAll(async ({ baseURL }) => requireStack(baseURL));
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("filex-theme", "light");
    } catch {
      // storage blocked
    }
  });
});

/** The "page N of 10" number printed on a text_10p.pdf page (0 if none). */
function printedNumber(page: PDFPage): number {
  const contents = page.node.Contents();
  const streams = contents instanceof PDFArray ? contents.asArray().map((r) => page.doc.context.lookup(r)) : [contents];
  const text = streams
    .filter((s): s is PDFRawStream => s instanceof PDFRawStream)
    .map((s) => Buffer.from(decodePDFRawStream(s).decode()).toString("latin1"))
    .join("\n");
  return Number(/page (\d+) of 10/.exec(text)?.[1] ?? 0);
}

async function scan(page: Page, info: TestInfo, label: string) {
  await settle(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const lines = result.violations.map((v) => `[${v.impact}] ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  await info.attach(`axe-${label}.txt`, { body: lines.join("\n") || "no violations", contentType: "text/plain" });
  const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), label).toEqual([]);
}

const tile = (page: Page, original: number) =>
  page.getByRole("option", { name: new RegExp(`page ${original} of the original`, "i") }).first();

test("organize: keyboard reorder, turn, duplicate, delete and drag give the expected PDF", async ({ page }, info) => {
  test.setTimeout(JOB_TIMEOUT + 90_000);
  const csp: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text());
  });
  await openTool(page, "pdf.organize");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/text_10p.pdf"));
  const grid = page.getByRole("listbox", { name: "Pages" });
  await expect(grid.getByRole("option")).toHaveCount(10);

  // Thumbnails: PDF.js renders in <img> (blob: URLs), never a frame.
  const thumb = grid.locator(".pg-frame img").first();
  await expect(thumb).toHaveAttribute("src", /^blob:/, { timeout: 30_000 });
  expect(await thumb.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(50);
  await expect(page.locator("iframe, object, embed")).toHaveCount(0);
  await grid.evaluate((el) => el.closest("section")!.scrollIntoView({ block: "start" }));
  await shot(page, "organize-grid");
  await scan(page, info, "organize-light");

  // Keyboard only: focus page 2, select it, move it earlier; the move is announced.
  await grid.getByRole("option").first().focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Space");
  await page.keyboard.press("Control+ArrowLeft");
  await expect(page.locator(".pg [role=status]")).toHaveText("Page 2 of the original moved earlier, now page 1.");
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", "Page 1, page 2 of the original");
  await page.keyboard.press("Escape");
  // Order now 2,1,3,4,...,10; focus on the first tile.

  // Keyboard delete: arrow to page 5 (index 4), Delete; then to page 7 (index 6), Backspace.
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Delete");
  await expect(tile(page, 5)).toHaveAttribute("aria-label", "Page 5 of the original, deleted");
  await expect(page.locator(".pg [role=status]")).toContainText("deleted. 9 pages in the result.");
  for (let i = 0; i < 2; i++) await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Backspace");
  await expect(tile(page, 7)).toHaveAttribute("aria-label", "Page 7 of the original, deleted");

  // Turn page 3 right: the grid has no rotate key, so select it with Space and press the toolbar
  // button with Enter (still keyboard only).
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft"); // index 2 = page 3
  await page.keyboard.press("Space");
  await expect(tile(page, 3)).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Turn right" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".pg [role=status]")).toContainText("turned right, now 90°");
  await tile(page, 3).click(); // deselect
  await expect(grid.locator("[aria-selected=true]")).toHaveCount(0);

  // Toolbar with the mouse: duplicate page 1.
  await tile(page, 1).click();
  await page.getByRole("button", { name: "Duplicate" }).click();
  await tile(page, 1).click();
  await expect(grid.locator("[aria-selected=true]")).toHaveCount(0);
  // Order now 2,1,1,3@90,4,(5),6,(7),8,9,10.

  // Drag page 10 before page 4.
  await tile(page, 10).dragTo(tile(page, 4), { targetPosition: { x: 5, y: 40 } });
  // Order now 2,1,1,3@90,10,4,6,8,9.
  await expect(page.getByText("The result will have 9 pages.", { exact: false })).toBeVisible();

  await page.setViewportSize({ width: 360, height: 780 });
  await expectNoHorizontalScroll(page);
  await grid.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await shot(page, "organize-grid-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });

  await runButton(page).click();
  await waitForResult(page);
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  const doc = await PDFDocument.load(bytes);
  expect(doc.getPages().map(printedNumber)).toEqual([2, 1, 1, 3, 10, 4, 6, 8, 9]);
  expect(doc.getPages().map((p) => p.getRotation().angle % 360)).toEqual([0, 0, 0, 90, 0, 0, 0, 0, 0]);
  expect(csp).toEqual([]);
  await deleteNow(page);
});

test("organize: the grid passes axe in dark theme", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("filex-theme", "dark"));
  await openTool(page, "pdf.organize");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/mixed_pages.pdf"));
  const grid = page.getByRole("listbox", { name: "Pages" });
  await expect(grid.locator(".pg-frame img").first()).toHaveAttribute("src", /^blob:/, { timeout: 30_000 });
  await grid.getByRole("option").first().click();
  await grid.evaluate((el) => el.closest("section")!.scrollIntoView({ block: "start" }));
  await shot(page, "organize-grid-dark");
  await scan(page, info, "organize-dark");
});
