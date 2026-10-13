// Plain-language text for API codes and sizes. Server messages are shown under these titles as
// text; the code goes in brackets so a report can quote it.

const ERROR_TITLES: Record<string, string> = {
  NETWORK: "FileX did not answer.",
  UNAUTHENTICATED: "Sign-in is needed to run tools.",
  USER_NOT_ALLOWED: "This account is not on the FileX list. Ask your admin to add it.",
  AUTH_UNAVAILABLE: "Sign-in server unreachable. Try again in a few seconds.",
  TOO_MANY_REQUESTS: "Too many requests. Wait a minute, then try again.",
  TOOL_NOT_FOUND: "This tool is not on the server any more.",
  TOOL_UNAVAILABLE: "This tool is off right now.",
  INVALID_INPUT_FORMAT: "A file is not a type this tool accepts.",
  INVALID_FILE_COUNT: "This tool needs a different number of files.",
  TOO_MANY_FILES: "Too many files for one job.",
  MISSING_PART: "A required file is missing.",
  FILE_TOO_LARGE: "A file is too large for this server.",
  UPLOAD_TOO_LARGE: "The files are too large together for one job.",
  TOO_MANY_ARCHIVE_ENTRIES: "An archive holds too many files.",
  INVALID_OPTIONS: "Some options are not valid. Check the highlighted fields.",
  TARGET_REQUIRED: "Choose a format to convert to.",
  INVALID_TARGET: "These files cannot be converted to that format. Pick another format or change the files.",
  OPTIONS_TOO_LARGE: "The options are too long.",
  NO_ROUTE: "FileX cannot convert these files to that format right now.",
  UNKNOWN_FORMAT: "FileX does not know one of these formats.",
  QUEUE_FULL: "FileX is busy. Try again in a minute.",
  JOB_NOT_FOUND: "This job is gone. It expired or was deleted.",
  JOB_NOT_READY: "The result is not ready yet.",
  OUTPUT_NOT_FOUND: "This file is gone from the server.",
  INVALID_PDF: "One of the files is not a readable PDF.",
  ENCRYPTED_PDF: "A PDF is password-protected. Remove the password first, then try again.",
  CANCELLED: "The job was cancelled.",
  TIMEOUT: "The job took too long and was stopped.",
  INTERNAL_ERROR: "Something went wrong on the server.",
};

export function errorTitle(code: string, fallback: string): string {
  return ERROR_TITLES[code] ?? fallback;
}

const WARNINGS: Record<string, string> = {
  INPUT_REPAIRED: "A damaged input was repaired before processing. Check the result.",
  RESTRICTIONS_REMOVED: "An input had copy or print restrictions; the result does not keep them.",
  LOSSY_STEP: "One step may lose some formatting, layout or image quality.",
  OCR_SKIPPED: "Text recognition was skipped because the file already has text.",
};

export function warningText(code: string, serverMessage: string): string {
  return WARNINGS[code] ?? serverMessage;
}

/** 1536 → "1.5 KB". Binary units, matching the server's MB limits. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** "in 23 h", "in 12 min", or null once past. */
export function expiresIn(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso) - now;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const min = Math.ceil(ms / 60_000);
  return min < 60 ? `in ${min} min` : `in ${Math.round(min / 60)} h`;
}
