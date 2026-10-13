// Keel (Supabase-compatible Auth) client. Created only after GET /config names the server, so a
// published build carries no owner-specific URL or key (ADR-0009, spec A9).
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Config } from "../api/client";

/** Where Keel lives: /config first; VITE_KEEL_* only on the dev server (dropped from builds). */
export function keelTarget(config: Config): { url: string; key: string } | null {
  if (config.keelUrl && config.keelAnonKey) return { url: config.keelUrl, key: config.keelAnonKey };
  if (import.meta.env.DEV && import.meta.env.VITE_KEEL_URL && import.meta.env.VITE_KEEL_ANON_KEY) {
    return { url: import.meta.env.VITE_KEEL_URL, key: import.meta.env.VITE_KEEL_ANON_KEY };
  }
  return null;
}

let client: { url: string; keel: Promise<SupabaseClient> } | null = null;

/**
 * One client per page (StrictMode runs effects twice). Session in localStorage, refreshed
 * automatically: accepted for R1 (review 5.3). Implicit flow on purpose: invite links are started
 * by the admin in Keel Studio, so no PKCE verifier exists in this browser and Keel returns the
 * session in the URL fragment (`#access_token=...&type=invite`), which a PKCE client refuses.
 * The fragment never reaches a server or a Referer. supabase-js does not read it
 * (`detectSessionInUrl: false`): `readCallback` strips it at start-up and AuthGate hands the
 * tokens of an invite or recovery link to `setSession` (review L-4, L-5).
 * Loaded on demand: open and token mode never download supabase-js.
 */
export function getKeel(url: string, key: string): Promise<SupabaseClient> {
  if (client?.url !== url) {
    client = {
      url,
      keel: import("@supabase/supabase-js").then(({ createClient }) =>
        createClient(url, key, {
          auth: { flowType: "implicit", persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
        }),
      ),
    };
    client.keel.catch(() => (client = null)); // a failed chunk load may be retried
  }
  return client.keel;
}

export type Tokens = { access_token: string; refresh_token: string };
export type Callback = { type: string | null; errorCode: string | null; tokens?: Tokens };

/**
 * Read what an email link put in the fragment and strip it from the address bar and the history
 * entry at once, in every auth mode (review L-4). A session is taken only from an invite or
 * recovery link: any other fragment with tokens may be someone else's session pushed onto this
 * browser (login CSRF, review L-5), so it is dropped. An error fragment is kept to say why.
 * `type` is set by whoever wrote the link, so this filter alone is not enough: AuthGate asks before
 * a link replaces the session of a different, signed-in user (review final L-1).
 */
export function readCallback(): Callback | null {
  const hash = window.location.hash.slice(1);
  if (!hash) return null;
  const p = new URLSearchParams(hash);
  if (!p.has("access_token") && !p.has("error") && !p.has("error_code")) return null;
  clearCallback();
  const type = p.get("type");
  const errorCode = p.get("error_code") ?? p.get("error");
  const access = p.get("access_token");
  const refresh = p.get("refresh_token");
  if (errorCode) return { type, errorCode };
  if ((type === "invite" || type === "recovery") && access && refresh) {
    return { type, errorCode: null, tokens: { access_token: access, refresh_token: refresh } };
  }
  return null;
}

/**
 * Who a link's access token names: `sub` and `email` from the JWT payload, NOT verified here.
 * Only used to tell whether a link is for the account already signed in, and to name the other
 * account in the switch prompt; `setSession` has Keel verify the token before it is used
 * (review final L-1: `type` is attacker-controlled, so a link never replaces a session silently).
 */
export function tokenUser(accessToken: string): { id: string | null; email: string | null } {
  try {
    const b64 = accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const claims: unknown = JSON.parse(new TextDecoder().decode(bytes));
    const { sub, email } = (claims ?? {}) as { sub?: unknown; email?: unknown };
    return { id: typeof sub === "string" ? sub : null, email: typeof email === "string" ? email : null };
  } catch {
    return { id: null, email: null };
  }
}

/** Drop tokens or error text from the address bar once the client has read them. */
export function clearCallback(): void {
  const { pathname, search } = window.location;
  window.history.replaceState(window.history.state, "", pathname + search);
}

/** Origin of the app: Keel accepts any page on the Site URL's origin as a redirect target. */
export function redirectTo(): string {
  return `${window.location.origin}/`;
}
