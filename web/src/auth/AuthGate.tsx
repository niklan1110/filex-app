// Decides what the page shows before anything else: GET /config, then by authMode
//   NONE  -> the app with the permanent open-mode banner;
//   TOKEN -> an fxt_ token field (token kept in sessionStorage for this tab);
//   KEEL  -> supabase-js sign-in against the Keel server named by /config.
// A signed-in principal is checked once with the authenticated GET /status, so a user who is not
// on the allowlist sees "not allowed" instead of an empty catalog. Later 401 / 403 / 503 answers
// from any call come back through setAuthErrorHandler.
import type { AuthError, SupabaseClient } from "@supabase/supabase-js";
import { useEffect, useRef, useState } from "react";
import { App } from "../App";
import {
  ApiError,
  apiGet,
  deleteSession,
  paths,
  setAuthErrorHandler,
  setTokenProvider,
  type Config,
} from "../api/client";
import { Alert } from "../components/Alert";
import { Footer } from "../components/Footer";
import { LimitsContext } from "../limits";
import { errorTitle } from "../messages";
import { SignIn, type Problem, type SignInView } from "../pages/SignIn";
import { getKeel, keelTarget, redirectTo, tokenUser, type Callback, type Tokens } from "./keel";

type View =
  | { kind: "loading" }
  | { kind: "offline"; error: ApiError }
  | { kind: "ready" }
  | SignInView;

const TOKEN_KEY = "filex-token";
const UNREACHABLE: Problem = {
  title: "Sign-in server unreachable",
  text: "FileX could not reach Keel. Check that it is running, then try again.",
};
const LINK_DEAD: Problem = {
  title: "This link has expired or was already used.",
  text: "Ask your admin for a new invite, or use “Forgot password?” for a new reset link.",
};

function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
function writeToken(t: string | null): void {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked: the token lives in memory for this page only
  }
}

/** Keel's error codes in plain words. Messages from Keel are shown as text only. */
function keelProblem(e: AuthError): Problem {
  if (!e.status || e.status >= 500 || e.name === "AuthRetryableFetchError") return UNREACHABLE;
  switch (e.code) {
    case "invalid_credentials":
      return { title: "Wrong email or password." };
    case "email_not_confirmed":
      return { title: "This address is not confirmed yet.", text: "Use the link in your invite email first." };
    case "user_banned":
      return { title: "This account is blocked. Ask your admin." };
    case "weak_password":
      return { title: "Choose a stronger password.", text: e.message };
    case "same_password":
      return { title: "That is your current password. Choose a new one." };
    case "insufficient_aal":
      return { title: "This account uses two-factor sign-in, which FileX does not support yet. Ask your admin." };
    case "over_request_rate_limit":
    case "over_email_send_rate_limit":
      return { title: "Too many attempts. Wait a minute, then try again." };
    case "otp_expired":
      return LINK_DEAD;
    default:
      return { title: "That did not work.", text: e.message };
  }
}

export function AuthGate({ callback }: { callback: Callback | null }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [view, setView] = useState<View>({ kind: "loading" });
  const [email, setEmail] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const keel = useRef<SupabaseClient | null>(null);
  const pending = useRef<{ tokens: Tokens; invite: boolean } | null>(null); // a link waiting for "Switch account?"
  const token = useRef<string | null>(null); // the Bearer value: Keel access token or fxt_ token
  const modeRef = useRef<Config["authMode"] | undefined>(undefined);
  const mode = config?.authMode;

  // One check of the principal; auth failures are mapped by the handler below.
  async function check() {
    setView({ kind: "loading" });
    try {
      await apiGet(paths.status());
      setView({ kind: "ready" });
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e));
      if (err.status !== 401 && err.status !== 403 && err.status !== 503) setView({ kind: "offline", error: err });
    }
  }

  useEffect(() => {
    let live = true;
    let unsubscribe = () => {};
    setTokenProvider(() => token.current);
    // 401 / 403 USER_NOT_ALLOWED / 503 AUTH_UNAVAILABLE from any API call.
    setAuthErrorHandler((err) => {
      const m = modeRef.current;
      if (!live || m === "NONE" || m === undefined) return;
      if (err.code === "AUTH_UNAVAILABLE") setView({ kind: "unreachable" });
      else if (err.code === "USER_NOT_ALLOWED") setView({ kind: "not-allowed" });
      else if (err.status === 401) {
        token.current = null;
        if (m === "TOKEN") {
          writeToken(null);
          setView({ kind: "signin", problem: { title: "That token was not accepted.", text: "Check it with your admin." } });
        } else {
          void keel.current?.auth.signOut({ scope: "local" });
          setView({ kind: "signin", note: "Your session ended. Sign in again." });
        }
      }
    });

    (async () => {
      let cfg: Config;
      try {
        cfg = await apiGet<Config>(paths.config());
      } catch (e) {
        if (live) setView({ kind: "offline", error: e instanceof ApiError ? e : new ApiError(0, "CLIENT", String(e)) });
        return;
      }
      if (!live) return;
      modeRef.current = cfg.authMode;
      setConfig(cfg);

      if (cfg.authMode === "NONE") {
        token.current = null;
        setView({ kind: "ready" });
        return;
      }
      if (cfg.authMode === "TOKEN") {
        token.current = token.current ?? readToken();
        if (token.current) void check();
        else setView({ kind: "signin" });
        return;
      }

      const target = keelTarget(cfg);
      if (!target) {
        setView({ kind: "offline", error: new ApiError(0, "CLIENT", "The server has no Keel address in GET /config.") });
        return;
      }
      let client: SupabaseClient;
      try {
        client = await getKeel(target.url, target.key);
      } catch {
        if (live) setView({ kind: "offline", error: new ApiError(0, "NETWORK", "The sign-in code did not load. Reload the page.") });
        return;
      }
      if (!live) return;
      keel.current = client;
      const { data: sub } = client.auth.onAuthStateChange((event, session) => {
        token.current = session?.access_token ?? null;
        setEmail(session?.user.email ?? null);
        if (event === "SIGNED_OUT") setView((v) => (v.kind === "signin" ? v : { kind: "signin" }));
        if (event === "PASSWORD_RECOVERY") setView({ kind: "set-password", invite: false });
      });
      unsubscribe = () => sub.subscription.unsubscribe();

      // An invite or recovery link's session, read from the fragment by readCallback (L-4, L-5).
      // A link for another account never replaces a signed-in session without asking (final L-1).
      if (callback?.tokens) {
        const { data: now } = await client.auth.getSession();
        if (!live) return;
        const current = now.session;
        const next = tokenUser(callback.tokens.access_token);
        if (current && current.user.id !== next.id) {
          pending.current = { tokens: callback.tokens, invite: callback.type === "invite" };
          token.current = current.access_token;
          setEmail(current.user.email ?? null);
          setView({ kind: "switch", next: next.email });
          return;
        }
        const { error } = await client.auth.setSession(callback.tokens);
        if (!live) return;
        if (error) {
          setView({ kind: "signin", problem: LINK_DEAD });
          return;
        }
      }
      const { data } = await client.auth.getSession();
      if (!live) return;
      const session = data.session;
      token.current = session?.access_token ?? null;
      setEmail(session?.user.email ?? null);
      if (callback?.errorCode) setView({ kind: "signin", problem: LINK_DEAD });
      else if (session && (callback?.type === "invite" || callback?.type === "recovery")) {
        setView({ kind: "set-password", invite: callback.type === "invite" });
      } else if (session) void check();
      else setView({ kind: "signin" });
    })();

    return () => {
      live = false;
      unsubscribe();
      setAuthErrorHandler(() => {});
    };
    // `attempt` re-runs the whole start-up after "Try again" on the offline page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  async function signIn(address: string, password: string): Promise<Problem | null> {
    const { data, error } = await keel.current!.auth.signInWithPassword({ email: address, password });
    if (error) return keelProblem(error);
    token.current = data.session.access_token;
    setEmail(data.user.email ?? null);
    void check();
    return null;
  }

  // The answer to "Switch account?": keep the current session unless the user chose the link's.
  async function decideSwitch(accept: boolean) {
    const link = pending.current;
    pending.current = null;
    if (!accept || !link) {
      void check();
      return;
    }
    const { data, error } = await keel.current!.auth.setSession(link.tokens);
    if (error || !data.session) {
      setView({ kind: "signin", problem: LINK_DEAD });
      return;
    }
    token.current = data.session.access_token;
    setEmail(data.session.user.email ?? null);
    setView({ kind: "set-password", invite: link.invite });
  }

  async function submitToken(value: string): Promise<Problem | null> {
    token.current = value;
    writeToken(value);
    void check();
    return null;
  }

  async function forgot(address: string): Promise<Problem | null> {
    const { error } = await keel.current!.auth.resetPasswordForEmail(address, { redirectTo: redirectTo() });
    return error ? keelProblem(error) : null;
  }

  async function setPassword(password: string): Promise<Problem | null> {
    const { error } = await keel.current!.auth.updateUser({ password });
    if (error) return keelProblem(error);
    void check();
    return null;
  }

  // DELETE /session first (drops the API's cached check), then end every Keel session.
  async function signOut() {
    try {
      await deleteSession();
    } catch {
      // the API cache entry dies on its own within 60 s; signing out of Keel matters more
    }
    let note = "You are signed out.";
    if (mode === "KEEL" && keel.current) {
      const { error } = await keel.current.auth.signOut({ scope: "global" });
      if (error) note = "Signed out on this device. Keel could not be reached, so other devices stay signed in until their session ends.";
    }
    token.current = null;
    writeToken(null);
    setEmail(null);
    setView({ kind: "signin", note });
  }

  if (view.kind === "loading") {
    return (
      <main className="main" id="main">
        <h1 className="sr-only">FileX</h1>
        <p className="state" role="status">Loading…</p>
      </main>
    );
  }
  if (view.kind === "offline") {
    return (
      <main className="main" id="main">
        <h1 className="page-title">FileX</h1>
        <div className="state">
          <Alert title={errorTitle(view.error.code, "FileX could not start.")} error={view.error}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </Alert>
        </div>
      </main>
    );
  }
  if (view.kind === "ready" && config) {
    const open = config.authMode === "NONE";
    return (
      <LimitsContext.Provider value={config.limits}>
        <App email={open ? null : email} onSignOut={open ? undefined : signOut} />
        <Footer version={config.version} />
      </LimitsContext.Provider>
    );
  }
  return (
    <>
      <SignIn
        mode={mode === "TOKEN" ? "TOKEN" : "KEEL"}
        view={view as SignInView}
        email={email}
        onSignIn={signIn}
        onToken={submitToken}
        onForgot={forgot}
        onSetPassword={setPassword}
        onRetry={() => void check()}
        onSignOut={() => void signOut()}
        onSwitch={decideSwitch}
      />
      <Footer version={config?.version} />
    </>
  );
}
