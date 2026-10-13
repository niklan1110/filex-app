// Real jobs against the live stack (FILEX_AUTH=none). Every job is deleted afterwards: "Delete now"
// in the UI where the flow has a result, and the jobIds fixture for anything left over.
import type { Locator, Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import {
  JOB_TIMEOUT,
  corpus,
  deleteNow,
  download,
  expect,
  expectNoHorizontalScroll,
  openTool,
  pdfHasText,
  pdfPageCount,
  requireStack,
  runButton,
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

/** Press Tab until `target` has focus (keyboard-only flows). */
async function tabTo(page: Page, target: Locator, max = 60): Promise<void> {
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement).catch(() => false)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached ${target}`);
}

test("merge two PDFs with the keyboard only, download a 13-page PDF, delete it", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 60_000);
  await page.goto("/");
  await expect(page.locator("a.tile").first()).toBeVisible();

  // Catalog: "/" then type, Tab to the tile, Enter.
  await page.keyboard.press("/");
  await page.keyboard.type("merge");
  const tile = page.getByRole("link", { name: /Merge PDFs/ });
  await tabTo(page, tile);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/tools\/pdf\.merge$/);
  await expect(page.getByRole("heading", { name: "Merge PDFs", level: 1 })).toBeVisible();

  // Choose files: Tab to the button, Space opens the system picker.
  const input = page.locator("#choose-files");
  await tabTo(page, input);
  const chooser = page.waitForEvent("filechooser");
  await page.keyboard.press("Space");
  await (await chooser).setFiles([corpus("pdf/text_10p.pdf"), corpus("pdf/scanned_3p.pdf")]);
  await expect(page.locator("ol.files li")).toHaveCount(2);
  await expect(page.locator("ol.files .fname").first()).toHaveText("text_10p.pdf");
  await shot(page, "tool-page");

  // Run with Enter on the submit button.
  await tabTo(page, runButton(page));
  await page.keyboard.press("Enter");
  const heading = await waitForResult(page);
  await expect(heading).toBeFocused(); // focus moves to the outcome

  const dlButton = page.getByRole("button", { name: /^Download \(/ });
  await tabTo(page, dlButton);
  const { dl, bytes } = await download(page, () => page.keyboard.press("Enter"));
  expect(dl.suggestedFilename()).toMatch(/\.pdf$/);
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
  expect(await pdfPageCount(bytes)).toBe(13);

  await tabTo(page, page.getByRole("button", { name: "Delete now" }));
  await page.keyboard.press("Enter");
  await expect(page.locator(".result h3.job-title")).toHaveText("Deleted from the server");
});

test("wrong file type is refused before upload, with a way out", async ({ page, jobIds }) => {
  await openTool(page, "pdf.merge");
  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));
  const alert = page.locator(".alert.refused");
  await expect(alert).toBeVisible();
  await expect(alert).toHaveAttribute("role", "alert");
  await expect(alert).toContainText("report.docx: Merge PDFs takes PDF files only.");
  await expect(alert.getByRole("link", { name: "See all tools" })).toBeVisible();
  await expect(page.locator("ol.files li")).toHaveCount(0);
  await shot(page, "error-wrong-type");
  expect(jobIds).toHaveLength(0);
});

test("a file with a .pdf name that is not a PDF is refused by the server", async ({ page, jobIds }) => {
  await openTool(page, "pdf.merge");
  await page.locator("#choose-files").setInputFiles([corpus("pdf/text_1p.pdf"), corpus("odd/zip_named.pdf")]);
  await expect(page.locator("ol.files li")).toHaveCount(2);
  await runButton(page).click();
  const alert = page.locator(".submit-bar [role=alert]");
  await expect(alert).toContainText("A file is not a type this tool accepts.");
  await expect(alert).toContainText("zip_named.pdf");
  await expect(alert).toContainText("INVALID_INPUT_FORMAT");
  expect(jobIds).toHaveLength(0);
});

test("an encrypted PDF gives the password message", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await openTool(page, "pdf.merge");
  await page.locator("#choose-files").setInputFiles([corpus("pdf/text_1p.pdf"), corpus("pdf/encrypted_aes256.pdf")]);
  await runButton(page).click();
  // The message is in a visible <strong> and in the hidden status line; match the visible one only.
  await expect(
    page.locator("strong", { hasText: "A PDF is password-protected. Remove the password first, then try again." }),
  ).toBeVisible({ timeout: JOB_TIMEOUT });
  await shot(page, "error-encrypted");
});

test("compress: pick a level, result card shows the size, download is smaller", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  const input = corpus("pdf/scanned_3p.pdf");
  const before = (await readFile(input)).length;
  await openTool(page, "pdf.compress");
  await page.locator("#choose-files").setInputFiles(input);
  await page.locator("input[name=opt-level][value=EXTREME]").check();
  await runButton(page).click();
  await waitForResult(page);
  const dlButton = page.getByRole("button", { name: /^Download \([\d.]+ (B|KB|MB)\)$/ });
  await expect(dlButton).toBeVisible();
  await expect(page.locator(".result .expires")).toContainText(/Expires in \d+ (h|min)/);
  await shot(page, "result-card");
  const { bytes } = await download(page, dlButton);
  expect(bytes.length).toBeLessThan(before);
  expect(await pdfPageCount(bytes)).toBe(3);
  await deleteNow(page);
});

test("compress: outcome card (−N %, old → new size, size bars) and page 1 before/after (spec 9.8)", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 60_000);
  const csp: string[] = []; // PDF.js and its worker must work under the strict CSP
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text());
  });
  await openTool(page, "pdf.compress");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/scanned_3p.pdf"));
  await page.locator("input[name=opt-level][value=EXTREME]").check();
  await runButton(page).click();
  await waitForResult(page);
  const result = page.locator(".result");

  // Poster percent (U+2212), the figure, and one sentence for screen readers.
  await expect(result.locator(".outcome")).toHaveText(/^−\d+%$/);
  await expect(result.locator(".outcome")).toHaveAttribute("aria-hidden", "true");
  await expect(result.locator(".figure")).toHaveText(/^[\d.]+ (KB|MB) → [\d.]+ (KB|MB)$/);
  await expect(result.locator(".outcome-line")).toHaveText(/^[\d.]+ (B|KB|MB) saved$/);
  const sizes = page.getByRole("img", { name: /^Original [\d.]+ (KB|MB), compressed [\d.]+ (B|KB|MB), \d+ percent smaller\.$/ });
  await expect(sizes).toBeVisible();
  await expect(sizes.locator(".size-bar")).toHaveCount(2);
  const pct = Number((await result.locator(".outcome").textContent())!.replace(/\D/g, ""));
  expect((await sizes.getAttribute("aria-label"))!).toContain(`${pct} percent smaller`);

  // Before/after: two PDF.js renders in <img> (blob: URLs), never a frame.
  const group = page.getByRole("group", { name: "Page 1, before and after" });
  const before = group.getByRole("img", { name: /^Page 1 of scanned_3p\.pdf before compression, / }).first();
  const after = group.getByRole("img", { name: /after compression, / }).first();
  await expect(before).toBeVisible({ timeout: 30_000 });
  await expect(after).toBeVisible();
  for (const img of [before, after]) {
    await expect(img).toHaveAttribute("src", /^blob:/);
    expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(100);
  }
  await expect(page.locator("iframe, object, embed")).toHaveCount(0);
  expect(csp).toEqual([]);
  await result.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await shot(page, "result-compress-light");

  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await result.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await shot(page, "result-compress-dark");
  await page.getByRole("radio", { name: "Light" }).check();

  // Under 40rem: one frame with a divider, moved with the keyboard.
  await page.setViewportSize({ width: 360, height: 780 });
  const slider = page.getByRole("slider", { name: /Compare position/ });
  await expect(slider).toBeVisible();
  await expect(group.locator(".ba-side")).toBeHidden();
  await slider.focus();
  for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowLeft");
  await expect(slider).toHaveAttribute("aria-valuetext", "45% before, 55% after");
  await expectNoHorizontalScroll(page);
  await group.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await shot(page, "result-compress-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });
  await deleteNow(page);
});

test("split: several outputs, per-file names, zip and single downloads", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await openTool(page, "pdf.split");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/text_10p.pdf"));
  await page.locator("#opt-every").fill("3");
  await runButton(page).click();
  const heading = await waitForResult(page);
  await expect(heading).toHaveText("4 files are ready");

  const names = page.locator(".result ol.files .fname");
  await expect(names).toHaveText([
    "text_10p-part-001.pdf",
    "text_10p-part-002.pdf",
    "text_10p-part-003.pdf",
    "text_10p-part-004.pdf",
  ]);
  await shot(page, "result-split");

  const one = await download(page, page.getByRole("button", { name: "Download text_10p-part-001.pdf" }));
  expect(one.dl.suggestedFilename()).toBe("text_10p-part-001.pdf");
  expect(await pdfPageCount(one.bytes)).toBe(3);

  const last = await download(page, page.getByRole("button", { name: "Download text_10p-part-004.pdf" }));
  expect(await pdfPageCount(last.bytes)).toBe(1);

  const all = await download(page, page.getByRole("button", { name: /^Download all \(zip, / }));
  expect(all.dl.suggestedFilename()).toMatch(/\.zip$/);
  expect(all.bytes.subarray(0, 2).toString("latin1")).toBe("PK");
  for (let n = 1; n <= 4; n++) expect(all.bytes.includes(Buffer.from(`text_10p-part-00${n}.pdf`))).toBe(true);
  await deleteNow(page);
});

test("office.convert: docx offers only reachable formats, Run waits for a choice, odt downloads", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await openTool(page, "office.convert");
  await expect(page.getByText("Add files to see the formats they can be converted to.")).toBeVisible();
  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));

  // Select (more than 4 targets) or radio cards (up to 4): read whichever is there.
  const select = page.locator("select#opt-target");
  const radios = page.locator("input[name=opt-target]");
  await expect(select.or(radios.first())).toBeVisible();
  const offered = (await select.count())
    ? await select.locator("option:not([disabled])").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value))
    : await radios.evaluateAll((rs) => rs.map((r) => (r as HTMLInputElement).value));
  expect(offered).toContain("odt");
  for (const unreachable of ["xlsx", "ods", "csv", "pptx", "odp", "xls", "ppt"]) expect(offered).not.toContain(unreachable);

  await expect(runButton(page)).toBeDisabled();
  await expect(page.locator(".submit-bar .fsize")).toContainText("choose a format");
  if (await select.count()) await select.selectOption("odt");
  else await page.locator("input[name=opt-target][value=odt]").check();
  await expect(runButton(page)).toBeEnabled();
  await runButton(page).click();
  await waitForResult(page);

  const { dl, bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(dl.suggestedFilename()).toMatch(/\.odt$/);
  // An ODT is a zip whose first entry is the uncompressed "mimetype" file.
  expect(bytes.subarray(0, 2).toString("latin1")).toBe("PK");
  expect(bytes.subarray(30, 100).toString("latin1")).toContain("mimetypeapplication/vnd.oasis.opendocument.text");
  await deleteNow(page);
});

test("images to PDF: a JPEG and a PNG become one 2-page PDF", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await openTool(page, "image.to-pdf");
  await page.locator("#choose-files").setInputFiles([corpus("image/photo_exif.jpg"), corpus("image/graphic_rgba.png")]);
  await expect(page.locator("ol.files li")).toHaveCount(2);
  await expect(runButton(page)).toBeEnabled(); // one target only: no format to choose
  await runButton(page).click();
  await waitForResult(page);
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(await pdfPageCount(bytes)).toBe(2);
  await deleteNow(page);
});

test("OCR: a scanned PDF gets a text layer", async ({ page }) => {
  test.setTimeout(240_000);
  const input = corpus("pdf/scanned_3p.pdf");
  expect(await pdfHasText(await readFile(input))).toBe(false);
  await openTool(page, "ocr.pdf");
  await page.locator("#choose-files").setInputFiles(input);
  await runButton(page).click();
  await waitForResult(page, 180_000);
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(await pdfPageCount(bytes)).toBe(3);
  expect(await pdfHasText(bytes)).toBe(true);
  await deleteNow(page);
});

test("watermark with Cyrillic text", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  const input = corpus("pdf/text_1p.pdf");
  await openTool(page, "pdf.watermark");
  await page.locator("#choose-files").setInputFiles(input);
  await page.locator("#opt-text").fill("Конфиденциально – ёЁ");
  await runButton(page).click();
  await waitForResult(page);
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(await pdfPageCount(bytes)).toBe(1);
  expect(bytes.equals(await readFile(input))).toBe(false);
  await deleteNow(page);
});

test("My jobs lists a finished job with its download", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await openTool(page, "pdf.merge");
  await page.locator("#choose-files").setInputFiles([corpus("pdf/text_1p.pdf"), corpus("pdf/text_10p.pdf")]);
  await runButton(page).click();
  await waitForResult(page);

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "My jobs" }).click();
  await expect(page.getByRole("heading", { name: "My jobs", level: 1 })).toBeVisible();
  const newest = page.locator("ul.jobs > li").first();
  await expect(newest).toContainText("Merge PDFs");
  await expect(newest).toContainText("2 files");
  await expect(newest.locator(".badge")).toHaveText("Done");
  await expect(newest).toContainText(/Expires in \d+ (h|min)/);
  await expectNoHorizontalScroll(page);
  await shot(page, "my-jobs");
  const { bytes } = await download(page, newest.getByRole("button", { name: /^Download/ }));
  expect(await pdfPageCount(bytes)).toBe(11);
  // The jobIds fixture deletes this job (My jobs has no delete button).
});
