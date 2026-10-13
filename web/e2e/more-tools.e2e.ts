// The remaining tools, each a short real job through the UI against the live stack, with the
// download checked (pdf-lib, zip entries, magic bytes). Every job is deleted: "Delete now" where the
// flow ends on a result card, the jobIds fixture for the rest.
import type { Page } from "@playwright/test";
import { PDFArray, PDFDocument, PDFRawStream, decodePDFRawStream, type PDFPage } from "pdf-lib";
import {
  JOB_TIMEOUT,
  corpus,
  deleteNow,
  download,
  expect,
  openTool,
  pdfHasText,
  pdfPageCount,
  pdfStreamsText,
  requireStack,
  runButton,
  test,
  waitForResult,
  zipEntries,
} from "./helpers";

test.beforeAll(async ({ baseURL }) => requireStack(baseURL));
test.beforeEach(async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 60_000);
  await page.addInitScript(() => {
    try {
      localStorage.setItem("filex-theme", "light");
    } catch {
      // storage blocked
    }
  });
});

type FileArg = string | { name: string; mimeType: string; buffer: Buffer };

/** Open a tool, add files, let `fill` set options, run, wait for Done and download the single result. */
async function runOne(page: Page, tool: string, files: FileArg | FileArg[], fill?: () => Promise<void>) {
  await openTool(page, tool);
  await page.locator("#choose-files").setInputFiles(files as never);
  if (fill) await fill();
  await runButton(page).click();
  await waitForResult(page);
  return download(page, page.getByRole("button", { name: /^Download \(/ }));
}

/** Pick the job's target format, select or radio cards, whichever the UI shows. */
async function pickTarget(page: Page, fmt: string) {
  const select = page.locator("select#opt-target");
  const radios = page.locator("input[name=opt-target]");
  await expect(select.or(radios.first())).toBeVisible();
  if (await select.count()) await select.selectOption(fmt);
  else await page.locator(`input[name=opt-target][value=${fmt}]`).check();
}

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

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

test.describe("page tools on text_10p.pdf", () => {
  test("rotate: pages 1-2 by 180°", async ({ page }) => {
    const { bytes } = await runOne(page, "pdf.rotate", corpus("pdf/text_10p.pdf"), async () => {
      await page.locator("input[name=opt-angle][value='180']").check();
      await page.locator("#opt-pages").fill("1-2");
    });
    const doc = await PDFDocument.load(bytes);
    expect(doc.getPages().map((p) => p.getRotation().angle % 360)).toEqual([180, 180, 0, 0, 0, 0, 0, 0, 0, 0]);
    await deleteNow(page);
  });

  test("extract-pages: 2-4 keeps three pages in order", async ({ page }) => {
    const { bytes } = await runOne(page, "pdf.extract-pages", corpus("pdf/text_10p.pdf"), () => page.locator("#opt-pages").fill("2-4"));
    expect((await PDFDocument.load(bytes)).getPages().map(printedNumber)).toEqual([2, 3, 4]);
    await deleteNow(page);
  });

  test("remove-pages: 1 and 10 go", async ({ page }) => {
    const { bytes } = await runOne(page, "pdf.remove-pages", corpus("pdf/text_10p.pdf"), () => page.locator("#opt-pages").fill("1, 10"));
    expect((await PDFDocument.load(bytes)).getPages().map(printedNumber)).toEqual([2, 3, 4, 5, 6, 7, 8, 9]);
    await deleteNow(page);
  });

  test("page-numbers: a custom template is stamped", async ({ page }) => {
    const { bytes } = await runOne(page, "pdf.page-numbers", corpus("pdf/text_10p.pdf"), async () => {
      await page.locator("#opt-template").fill("No. {n}/{total}");
      await page.locator("#opt-position").selectOption("top-right");
    });
    expect(await pdfPageCount(bytes)).toBe(10);
    const text = await pdfStreamsText(bytes);
    expect(text).toMatch(/No\. 3\/10/);
    await deleteNow(page);
  });
});

test("metadata-edit: title and author are written", async ({ page }) => {
  const { bytes } = await runOne(page, "pdf.metadata-edit", corpus("pdf/metadata.pdf"), async () => {
    await page.locator("#opt-title").fill("QA Title Ж");
    await page.locator("#opt-author").fill("QA Tester");
  });
  const doc = await PDFDocument.load(bytes, { updateMetadata: false });
  expect(doc.getTitle()).toBe("QA Title Ж");
  expect(doc.getAuthor()).toBe("QA Tester");
  await deleteNow(page);
});

test("pdf.to-image: three pages become a zip of PNGs", async ({ page }) => {
  await openTool(page, "pdf.to-image");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/text_10p.pdf"));
  await page.locator("#opt-pages").fill("1-3");
  await page.locator("#opt-dpi").fill("72");
  await runButton(page).click();
  await waitForResult(page);
  const all = await download(page, page.getByRole("button", { name: /^Download all \(zip, / }));
  const entries = zipEntries(all.bytes);
  expect(entries.map((e) => e.name).sort()).toEqual(["text_10p-p001.png", "text_10p-p002.png", "text_10p-p003.png"]);
  for (const e of entries) {
    expect(e.data.subarray(0, 8).equals(PNG)).toBe(true);
    expect(e.data.readUInt32BE(16)).toBeLessThan(700); // 72 dpi: an A4 page is 596 px wide
  }
  await deleteNow(page);
});

test("pdf.to-text: the text of every page", async ({ page }) => {
  const { dl, bytes } = await runOne(page, "pdf.to-text", corpus("pdf/text_10p.pdf"));
  expect(dl.suggestedFilename()).toBe("text_10p.txt");
  const text = bytes.toString("utf8");
  for (const n of [1, 5, 10]) expect(text).toContain(`page ${n} of 10`);
  await deleteNow(page);
});

test("repair: broken_xref.pdf comes back with a working xref and says so", async ({ page }) => {
  const { bytes } = await runOne(page, "pdf.repair", corpus("pdf/broken_xref.pdf"));
  await expect(page.locator(".job")).toContainText("[INPUT_REPAIRED]");
  // startxref now points at a real xref table or xref stream object.
  const tail = bytes.subarray(-64).toString("latin1");
  const at = Number(/startxref\s+(\d+)/.exec(tail)?.[1]);
  expect(bytes.subarray(at, at + 20).toString("latin1")).toMatch(/^(xref|\d+ \d+ obj)/);
  expect(await pdfPageCount(bytes)).toBeGreaterThan(0);
  await deleteNow(page);
});

test("PDF/A: the output declares PDF/A-2B in its XMP", async ({ page }) => {
  const { bytes } = await runOne(page, "pdf.to-pdfa", corpus("pdf/text_1p.pdf"));
  const xmp = bytes.toString("latin1") + (await pdfStreamsText(bytes));
  expect(xmp).toMatch(/pdfaid:part(=["']|>)2/);
  expect(xmp).toMatch(/pdfaid:conformance(=["']|>)B/);
  await deleteNow(page);
});

test("encrypt then decrypt: a round trip with a password", async ({ page }) => {
  const enc = await runOne(page, "pdf.encrypt", corpus("pdf/text_1p.pdf"), async () => {
    await page.locator("#opt-userPassword").fill("qa-secret-1");
  });
  expect(enc.bytes.toString("latin1")).toContain("/Encrypt");
  await expect(PDFDocument.load(enc.bytes)).rejects.toThrow(/encrypt/i);
  await deleteNow(page);

  const dec = await runOne(page, "pdf.decrypt", { name: "locked.pdf", mimeType: "application/pdf", buffer: enc.bytes }, async () => {
    await page.locator("#opt-password").fill("qa-secret-1");
  });
  expect(dec.bytes.toString("latin1")).not.toContain("/Encrypt");
  expect(await pdfPageCount(dec.bytes)).toBe(1);
  expect(await pdfHasText(dec.bytes)).toBe(true);
  await deleteNow(page);
});

test.describe("compress with a target size", () => {
  test("a reachable target: Target reached", async ({ page }) => {
    const { bytes } = await runOne(page, "pdf.compress", corpus("pdf/scanned_3p.pdf"), () => page.locator("#opt-targetMb").fill("0.2"));
    await expect(page.locator(".result")).toContainText("Target reached");
    expect(bytes.length).toBeLessThanOrEqual(0.2 * 1024 * 1024);
    await deleteNow(page);
  });

  test("an unreachable target: Best we could do", async ({ page }) => {
    await runOne(page, "pdf.compress", corpus("pdf/scanned_3p.pdf"), () => page.locator("#opt-targetMb").fill("0.05"));
    await expect(page.locator(".result")).toContainText("Best we could do");
    await expect(page.locator(".result")).toContainText("(target 0.05 MB)");
    await deleteNow(page);
  });
});

test("scan.clean: the blank page goes and the warning says so", async ({ page }) => {
  const { bytes } = await runOne(page, "scan.clean", corpus("pdf/scanned_3p.pdf"));
  await expect(page.locator(".job")).toContainText("[BLANK_PAGES_REMOVED]");
  expect(await pdfPageCount(bytes)).toBe(2);
  await deleteNow(page);
});

test.describe("ebook tools", () => {
  test("ebook.convert: epub → mobi", async ({ page }) => {
    const { dl, bytes } = await runOne(page, "ebook.convert", corpus("ebook/book.epub"), () => pickTarget(page, "mobi"));
    expect(dl.suggestedFilename()).toBe("book.mobi");
    expect(bytes.subarray(60, 68).toString("latin1")).toBe("BOOKMOBI");
    await deleteNow(page);
  });

  test("djvu.to-pdf: four pages", async ({ page }) => {
    const { bytes } = await runOne(page, "djvu.to-pdf", corpus("ebook/un_resolution_1837.djvu"));
    expect(await pdfPageCount(bytes)).toBe(4);
    await deleteNow(page);
  });

  test("comic.to-pdf: cbz → five pages", async ({ page }) => {
    const { bytes } = await runOne(page, "comic.to-pdf", corpus("ebook/comic.cbz"));
    expect(await pdfPageCount(bytes)).toBe(5);
    await deleteNow(page);
  });

  test("pdf.to-cbz: a zip of page images", async ({ page }) => {
    const { dl, bytes } = await runOne(page, "pdf.to-cbz", corpus("pdf/text_10p.pdf"), () =>
      page.locator("input[name=opt-imageFormat][value=png]").check(),
    );
    expect(dl.suggestedFilename()).toBe("text_10p.cbz");
    const pages = zipEntries(bytes).filter((e) => /\.png$/i.test(e.name));
    expect(pages).toHaveLength(10);
    expect(pages.every((e) => e.data.subarray(0, 8).equals(PNG))).toBe(true);
    await deleteNow(page);
  });
});

test.describe("office.to-pdf", () => {
  for (const [file, pages] of [["office/budget.xlsx", 1], ["office/slides.pptx", 3]] as const) {
    test(`${file.split("/")[1]} → pdf`, async ({ page }) => {
      const { bytes } = await runOne(page, "office.to-pdf", corpus(file));
      expect(await pdfPageCount(bytes)).toBeGreaterThanOrEqual(pages);
      await deleteNow(page);
    });
  }
});

test("cert.create then pdf.sign with that .p12 and its passphrase: the PDF has a signature", async ({ page }) => {
  const pass = "correct horse battery staple";
  await page.goto("/tools/cert.create");
  await expect(page.getByRole("heading", { name: /certificate/i, level: 1 })).toBeVisible();
  await page.locator("#opt-commonName").fill("QA Tester");
  await page.locator("#opt-passphrase").fill(pass);
  await runButton(page).click();
  await waitForResult(page);
  const cert = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(cert.dl.suggestedFilename()).toMatch(/\.p12$/);
  expect(cert.bytes[0]).toBe(0x30); // DER SEQUENCE
  await deleteNow(page);

  await openTool(page, "pdf.sign");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/text_1p.pdf"));
  await page.locator("input[type=file][aria-labelledby=aux-certificate-label]").setInputFiles({
    name: "qa.p12",
    mimeType: "application/x-pkcs12",
    buffer: cert.bytes,
  });
  await expect(page.locator(".aux .fsize").first()).toHaveText("qa.p12");
  await page.locator("#opt-passphrase").fill(pass);
  await page.locator("#opt-reason").fill("E2E");
  await runButton(page).click();
  await waitForResult(page);
  const signed = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  const raw = signed.bytes.toString("latin1");
  expect(raw).toMatch(/\/ByteRange\s*\[\s*0\s+\d+\s+\d+\s+\d+\s*\]/);
  expect(raw).toMatch(/\/Type\s*\/Sig\b/);
  expect(raw).toMatch(/\/SubFilter\s*\/(ETSI\.CAdES\.detached|adbe\.pkcs7\.detached)/);
  await deleteNow(page);
});
