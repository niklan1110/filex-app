import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import { Icon } from "./Icon";

/** Danger alert: what happened (bold), what to do, the error code in brackets. */
export function Alert({ title, error, children }: { title: string; error?: ApiError; children?: ReactNode }) {
  return (
    <div className="alert" role="alert">
      <Icon name="alert" size={18} />
      <div>
        <strong>{title}</strong>
        {error && (
          <p>
            {error.message} <span className="code">[{error.code}]</span>
          </p>
        )}
        {children}
      </div>
    </div>
  );
}

/** Message for an API failure on a read, with the auth seam's 401 case spelled out. */
export function loadFailedTitle(error: ApiError, what: string): string {
  if (error.status === 401) return `Sign-in is needed to see ${what}.`;
  return `Could not load ${what}.`;
}
