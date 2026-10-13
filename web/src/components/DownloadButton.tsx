import { useState, type ReactNode } from "react";
import { ApiError, browser, mintDownload } from "../api/client";

/**
 * Download on click: POST .../link for a fresh signed URL, then hand it to the browser. A button,
 * not a link, because the URL exists only after the click and is never kept (ADR-0009). A link
 * that expired before the browser got to it (404 from /dl) is fixed by clicking again.
 */
export function DownloadButton({ jobId, index, className, children, onError }: {
  jobId: string;
  index?: number;
  className: string;
  children: ReactNode;
  onError: (e: ApiError) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function click() {
    setBusy(true);
    try {
      browser.open(await mintDownload(jobId, index));
    } catch (e) {
      onError(e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className={className} aria-busy={busy || undefined} disabled={busy} onClick={click}>
      {children}
    </button>
  );
}
