// Shared fixtures and checks for the FileX E2E suite (tests/e2e/README.md).
import { test as base, expect, type Download, type Locator, type Page } from "@playwright/test";
import { readFile, mkdir } from "node:fs/promises";
import { inflateRawSync } from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PDFDocument, PDFName, PDFRawStream, decodePDFRawStream } from "pdf-lib";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");

/** Absolute path of a committed corpus file, e.g. corpus("pdf/text_1p.pdf"). */
export const corpus = (rel: string) => path.join(ROOT, "tests", "corpus", rel);

/** Generous wait for jobs on weak hardware (OCR and LibreOffice). */
export const JOB_TIMEOUT = 120_000;

/**
 * `test` with an automatic clean-up: every job this test creates (seen as a 2xx answer to
 * POST /api/v1/tools/{id}/jobs or /api/v1/convert/jobs) is deleted afterwards, even when the test fails before it gets
 * to "Delete now". A 404 means the UI already deleted it.
 */
export const test = base.extend<{ jobIds: string[] }>({
  jobIds: [
    async ({ page, request }, use) => {
      const ids: string[] = [];
      page.on("response", async (res) => {
        if (res.request().method() !== "POST" || !/\/api\/v1\/(tools\/[^/]+|convert)\/jobs$/.test(res.url())) return;
        if (!res.ok()) return;
        const body = await res.json().catch(() => null);
        if (body && typeof body.id === "string") ids.push(body.id);
      });
      await use(ids);
      for (const id of ids) {
        const res = await request.delete(`/api/v1/jobs/${id}`);
        expect([200, 202, 204, 404], `clean-up of ${id}`).toContain(res.status());
      }
    },
    { auto: true },
  ],
});
export { expect };

/** Fail fast with a clear message when the stack is not up (live specs only). */
export async function requireStack(baseURL: string | undefined): Promise<void> {
  const url = `${baseURL}/api/v1/config`;
  let ok = false;
  try {
    ok = (await fetch(url, { signal: AbortSignal.timeout(5000) })).ok;
  } catch {
    ok = false;
  }
  if (!ok) throw new Error(`FileX is not answering at ${url}. Start it with docker compose up (FILEX_AUTH=none).`);
}

/** Open a tool page and wait until its form is there. */
export async function openTool(page: Page, id: string): Promise<void> {
  await page.goto(`/tools/${id}`);
  await expect(page.locator("#choose-files")).toBeAttached();
}

/** The sticky run button: its name is the tool title followed by an arrow. */
export function runButton(page: Page): Locator {
  return page.locator(".submit-bar button[type=submit]");
}

/** Wait for the result card heading ("x.pdf is ready" or "3 files are ready"). */
export async function waitForResult(page: Page, timeout = JOB_TIMEOUT): Promise<Locator> {
  const heading = page.locator(".result h3.job-title");
  const badge = page.locator(".job .badge").first(); // the status; an outcome badge may follow
  await expect(badge).toHaveText(/Done|Failed|Cancelled/, { timeout });
  if ((await badge.textContent())?.trim() !== "Done") {
    const why = await page.locator(".job [role=alert]").allTextContents();
    throw new Error(`The job did not succeed: ${why.join(" ") || (await badge.textContent())}`);
  }
  await expect(heading).toHaveText(/is ready$|files are ready$/);
  return heading;
}

/** Click something that starts a download and return the downloaded bytes. */
export async function download(page: Page, trigger: Locator | (() => Promise<void>)): Promise<{ dl: Download; bytes: Buffer }> {
  const wait = page.waitForEvent("download", { timeout: 30_000 });
  if (typeof trigger === "function") await trigger();
  else await trigger.click();
  const dl = await wait;
  const file = await dl.path();
  return { dl, bytes: await readFile(file) };
}

/** Delete the job from the result card and check the UI says so. */
export async function deleteNow(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Delete now" }).click();
  await expect(page.locator(".result h3.job-title")).toHaveText("Deleted from the server");
}

export async function pdfPageCount(bytes: Buffer): Promise<number> {
  const doc = await PDFDocument.load(bytes);
  return doc.getPageCount();
}

/**
 * True when some content stream or form XObject draws text (BT ... Tj/TJ). Image-only scans
 * have none; OCRmyPDF adds an invisible text layer, often inside a form XObject.
 */
export async function pdfHasText(bytes: Buffer): Promise<boolean> {
  const doc = await PDFDocument.load(bytes);
  for (const [, obj] of doc.context.enumerateIndirectObjects()) {
    if (!(obj instanceof PDFRawStream)) continue;
    if (obj.dict.get(PDFName.of("Subtype")) === PDFName.of("Image")) continue;
    let text: string;
    try {
      text = Buffer.from(decodePDFRawStream(obj).decode()).toString("latin1");
    } catch {
      continue; // a filter pdf-lib cannot decode (images, fonts)
    }
    if (/\bBT\b[\s\S]*?(Tj|TJ)/.test(text)) return true;
  }
  return false;
}

/** The page does not scroll sideways (WCAG 1.4.10 reflow). */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(scroll, "page is wider than the viewport").toBeLessThanOrEqual(client);
}

/**
 * Wait for finite animations to end before an axe scan: mid-fade, axe sees washed-out colours and
 * reports color-contrast (the route preview fades in over 320 ms). Infinite ones, like spinners,
 * never end, so they are skipped.
 */
export async function settle(page: Page): Promise<void> {
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getComputedTiming().iterations === Infinity),
  );
}

/**
 * Screenshot for the owner under docs/design/screenshots/, only when E2E_SCREENSHOTS=1 so a normal
 * run does not rewrite committed images. Viewport only, to keep the PNGs small.
 */
export async function shot(page: Page, name: string): Promise<void> {
  if (process.env.E2E_SCREENSHOTS !== "1") return;
  const dir = path.join(ROOT, "docs", "design", "screenshots");
  await mkdir(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`), animations: "disabled" });
}

/** Entries of a zip (EPUB, CBZ, "Download all"): names and inflated bytes, from the central directory. */
export function zipEntries(bytes: Buffer): { name: string; data: Buffer }[] {
  const eocd = bytes.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (eocd < 0) throw new Error("not a zip: no end of central directory");
  const count = bytes.readUInt16LE(eocd + 10);
  let p = bytes.readUInt32LE(eocd + 16);
  const out: { name: string; data: Buffer }[] = [];
  for (let i = 0; i < count; i++) {
    if (bytes.readUInt32LE(p) !== 0x02014b50) throw new Error("bad central directory entry");
    const method = bytes.readUInt16LE(p + 10);
    const csize = bytes.readUInt32LE(p + 20);
    const [nlen, xlen, clen] = [bytes.readUInt16LE(p + 28), bytes.readUInt16LE(p + 30), bytes.readUInt16LE(p + 32)];
    const local = bytes.readUInt32LE(p + 42);
    const name = bytes.subarray(p + 46, p + 46 + nlen).toString("utf8");
    const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
    const raw = bytes.subarray(start, start + csize);
    out.push({ name, data: method === 0 ? raw : inflateRawSync(raw) });
    p += 46 + nlen + xlen + clen;
  }
  return out;
}

/** Every stream pdf-lib can decode, as latin1 text (content, XMP, form XObjects), joined. */
export async function pdfStreamsText(bytes: Buffer): Promise<string> {
  const doc = await PDFDocument.load(bytes, { ignoreEncryption: true });
  const parts: string[] = [];
  for (const [, obj] of doc.context.enumerateIndirectObjects()) {
    if (!(obj instanceof PDFRawStream)) continue;
    try {
      parts.push(Buffer.from(decodePDFRawStream(obj).decode()).toString("latin1"));
    } catch {
      // a filter pdf-lib cannot decode
    }
  }
  return parts.join("\n");
}
