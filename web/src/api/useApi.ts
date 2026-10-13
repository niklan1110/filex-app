import { useEffect, useState } from "react";
import { ApiError, apiGet } from "./client";

type Result<T> = { key: string; data?: T; error?: ApiError };

/**
 * GET a FileX API path; `retry()` refetches. Loading is derived, so no state is set in render.
 * A null path fetches nothing (loading false, no data).
 */
export function useApi<T>(path: string | null) {
  const [attempt, setAttempt] = useState(0);
  const [res, setRes] = useState<Result<T>>();
  const key = `${attempt}:${path}`;

  useEffect(() => {
    if (path === null) return;
    const ac = new AbortController();
    apiGet<T>(path, ac.signal).then(
      (data) => setRes({ key, data }),
      (e: unknown) => {
        if (ac.signal.aborted) return;
        const error = e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e));
        setRes({ key, error });
      },
    );
    return () => ac.abort();
  }, [key, path]);

  const current = res?.key === key ? res : undefined;
  return {
    data: current?.data,
    error: current?.error,
    loading: path !== null && !current,
    retry: () => setAttempt((n) => n + 1),
  };
}
