// PDF pages as images, rendered client-side by PDF.js (spec 9.7, ADR-0009: previews are only
// canvas renders shown in <img>). PDF.js and its worker load on the first call, never in the
// initial bundle. The worker is a same-origin module script (CSP script-src 'self'); PDF.js 6
// needs no eval. JPX/JBIG2 images need the wasm decoders, which are not shipped: such images are
// left blank in the preview and the rest of the page still renders.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?worker&url";

/** Files above this are not previewed: the bytes would sit in memory twice (page and worker). */
export const MAX_PREVIEW_BYTES = 50 * 1024 * 1024;
const RENDER_WIDTH = 900; // px; frames are at most ~360 CSS px wide, so this covers 2.5x screens
const MAX_PIXELS = 4_000_000; // caps very tall or very wide pages (16 MB of RGBA)

/** An open PDF. `destroy()` frees it (and the worker's copy); aborting the open signal does too. */
export type PdfDoc = {
  numPages: number;
  /** Page `n` (from 1) `width` px wide, as PNG, or JPEG when `jpeg` (smaller, for thumbnails). */
  render(n: number, width: number, signal: AbortSignal, jpeg?: boolean): Promise<Blob>;
  destroy(): Promise<void>;
};

/** Thrown for a password-protected PDF, so callers can say so. */
export class PdfPasswordError extends Error {}

export async function openPdf(data: ArrayBuffer, signal: AbortSignal): Promise<PdfDoc> {
  const pdfjs = await import("pdfjs-dist");
  signal.throwIfAborted();
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  const task = pdfjs.getDocument({
    data: new Uint8Array(data),
    enableXfa: false,
    maxImageSize: MAX_PIXELS * 4,
    canvasMaxAreaInBytes: MAX_PIXELS * 4,
    stopAtErrors: false,
  });
  const stop = () => void task.destroy();
  signal.addEventListener("abort", stop, { once: true });
  const destroy = async () => {
    signal.removeEventListener("abort", stop);
    await task.destroy();
  };
  let doc;
  try {
    doc = await task.promise;
  } catch (e) {
    await destroy();
    if (e instanceof Error && e.name === "PasswordException") throw new PdfPasswordError(e.message);
    throw e;
  }
  return {
    numPages: doc.numPages,
    destroy,
    async render(n, width, rsignal, jpeg = false) {
      const page = await doc.getPage(n);
      rsignal.throwIfAborted();
      const base = page.getViewport({ scale: 1 });
      const scale = Math.min(width / base.width, Math.sqrt(MAX_PIXELS / (base.width * base.height)));
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      const job = page.render({ canvas, viewport });
      const cancel = () => job.cancel();
      rsignal.addEventListener("abort", cancel, { once: true });
      try {
        await job.promise;
        const blob = await new Promise<Blob | null>((ok) =>
          canvas.toBlob(ok, jpeg ? "image/jpeg" : "image/png", jpeg ? 0.85 : undefined),
        );
        if (!blob) throw new Error("The page could not be turned into an image.");
        return blob;
      } finally {
        rsignal.removeEventListener("abort", cancel);
        canvas.width = canvas.height = 0; // free the bitmap now, not at the next GC
        page.cleanup();
      }
    },
  };
}

/** Render page 1 of `data` to a PNG blob. Aborting `signal` stops loading and rendering. */
export async function renderFirstPage(data: ArrayBuffer, signal: AbortSignal): Promise<Blob> {
  const doc = await openPdf(data, signal);
  try {
    return await doc.render(1, RENDER_WIDTH, signal);
  } finally {
    await doc.destroy();
  }
}
