import { createContext, useContext } from "react";
import type { Limits } from "./api/client";

// Server defaults (spec 7.1) until GET /config answers; the sign-in gate provides the real ones.
// The server checks every limit again; these only stop a doomed upload early.
export const DEFAULT_LIMITS: Limits = { maxUploadMb: 1024, maxFiles: 50, retentionMin: 1440 };
export const LimitsContext = createContext<Limits>(DEFAULT_LIMITS);

export function useLimits() {
  const l = useContext(LimitsContext);
  return { maxBytes: l.maxUploadMb * 1024 * 1024, maxFiles: l.maxFiles };
}
