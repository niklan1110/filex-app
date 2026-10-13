import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";
import { readCallback, tokenUser } from "./keel";

type Listener = (event: string, session: unknown) => void;

// One fake Keel auth client shared by every test (getKeel keeps a page-wide client).
const { auth, log } = vi.hoisted(() => {
  const log: string[] = [];
  const ok = async (...args: unknown[]): Promise<unknown> => ({ data: { args }, error: null });
  const auth = {
    listeners: [] as Listener[],
    session: null as null | { access_token: string; user: { id?: string; email: string } },
    onAuthStateChange: (cb: Listener) => {
      auth.listeners.push(cb);
      return { data: { subscription: { unsubscribe: () => (auth.listeners = auth.listeners.filter((l) => l !== cb)) } } };
    },
    getSession: async () => ({ data: { session: auth.session }, error: null }),
    setSession: async (tokens: { access_token: string; refresh_token: string }) => {
      log.push(`keel.setSession:${tokens.access_token}/${tokens.refresh_token}`);
      auth.session = { access_token: tokens.access_token, user: { email: "ana@home.lan" } };
      return { data: { session: auth.session }, error: null };
    },
    signInWithPassword: ok,
    resetPasswordForEmail: ok,
    updateUser: ok,
    signOut: async (o?: { scope: string }) => {
      log.push(`keel.signOut:${o?.scope}`);
      auth.session = null;
      auth.listeners.forEach((l) => l("SIGNED_OUT", null));
      return { error: null };
    },
  };
  return { auth, log };
});
vi.mock("@supabase/supabase-js", () => ({ createClient: vi.fn(() => ({ auth })) }));

const KEEL = {
  authMode: "KEEL", keelUrl: "https://keel.test/filex", keelAnonKey: "anon",
  limits: { maxUploadMb: 1024, maxFiles: 50, retentionMin: 1440 }, version: "1.0.0",
};
const SESSION = { access_token: "at-1", user: { id: "u-ana", email: "ana@home.lan" } };

/** An unsigned JWT-shaped access token: only its payload is read by the app. */
function jwt(claims: Record<string, unknown>): string {
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(claims)))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `eyJhbGciOiJIUzI1NiJ9.${b64}.sig`;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const err = (status: number, code: string) => json({ error: { code, message: code, details: {} } }, status);

let config: Record<string, unknown> = KEEL;
let status: () => Response = () => json({ groups: [], isolation: "container", version: "1" });

beforeEach(() => {
  window.history.pushState(null, "", "/");
  config = KEEL;
  status = () => json({ groups: [], isolation: "container", version: "1" });
  auth.session = null;
  auth.listeners = [];
  log.length = 0;
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const bearer = (init?.headers as Record<string, string>)?.Authorization ?? "-";
      log.push(`${init?.method ?? "GET"} ${url} ${bearer}`);
      if (url === "/api/v1/config") return json(config);
      if (url === "/api/v1/status") return status();
      if (url === "/api/v1/session") return new Response(null, { status: 204 });
      if (url === "/api/v1/tools") return json([]);
      if (url === "/api/v1/formats") return json([]);
      return err(404, "NOT_FOUND");
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function signInAs(email: string, password: string) {
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
}

describe("open mode", () => {
  it("shows the app with no banner, never creates a Keel client or sends a token", async () => {
    config = { ...KEEL, authMode: "NONE", keelUrl: null, keelAnonKey: null };
    const { createClient } = await import("@supabase/supabase-js");
    render(<AuthGate callback={null} />);
    expect(await screen.findByRole("main")).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(createClient).not.toHaveBeenCalled();
    expect(log.every((l) => l.endsWith(" -"))).toBe(true);
  });
});

describe("source footer (AGPL-3.0 section 13)", () => {
  it("shows the server's version and a source link in the app and on the sign-in screen", async () => {
    config = { ...KEEL, authMode: "NONE", keelUrl: null, keelAnonKey: null };
    render(<AuthGate callback={null} />);
    await screen.findByRole("main");
    expect(screen.getByRole("contentinfo").textContent).toContain("FileX 1.0.0");
    cleanup();

    config = KEEL;
    render(<AuthGate callback={null} />);
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByRole("contentinfo").textContent).toContain("FileX 1.0.0");
    expect(screen.getByRole("link", { name: "Source code" })).toBeTruthy();
  });
});

describe("Keel sign-in", () => {
  it("signs in, checks the principal with the Bearer token and signs out in the right order", async () => {
    const signIn = vi.spyOn(auth, "signInWithPassword").mockImplementation(async () => {
      auth.session = SESSION;
      return { data: { session: SESSION, user: SESSION.user }, error: null };
    });
    render(<AuthGate callback={null} />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/sign up|create account|register/i)).toBeNull(); // invite-only

    await signInAs("ana@home.lan", "correct horse battery");
    expect(signIn).toHaveBeenCalledWith({ email: "ana@home.lan", password: "correct horse battery" });
    await screen.findByRole("searchbox");
    expect(log).toContain("GET /api/v1/status Bearer at-1");
    expect(screen.getByText("ana@home.lan")).toBeTruthy();
    expect(log.some((l) => l.includes("at-1") && l.includes("?"))).toBe(false); // never in a URL

    log.length = 0;
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Sign out" })[0]));
    expect(log.slice(0, 2)).toEqual(["DELETE /api/v1/session Bearer at-1", "keel.signOut:global"]);
    expect(await screen.findByText("You are signed out.")).toBeTruthy();
  });

  it("maps wrong credentials and an unreachable Keel to plain messages", async () => {
    const signIn = vi.spyOn(auth, "signInWithPassword");
    signIn.mockResolvedValueOnce({ data: {}, error: { status: 400, code: "invalid_credentials", message: "x", name: "AuthApiError" } });
    signIn.mockResolvedValueOnce({ data: {}, error: { status: 0, message: "Failed to fetch", name: "AuthRetryableFetchError" } });
    render(<AuthGate callback={null} />);
    await signInAs("ana@home.lan", "nope");
    expect((await screen.findByRole("alert")).textContent).toContain("Wrong email or password.");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect((await screen.findByRole("alert")).textContent).toContain("Sign-in server unreachable");
  });

  it("shows 'not allowed' with a sign-out button instead of an empty catalog", async () => {
    auth.session = SESSION;
    status = () => err(403, "USER_NOT_ALLOWED");
    render(<AuthGate callback={null} />);
    expect((await screen.findByRole("alert")).textContent).toContain("This account is not on the FileX list.");
    expect(screen.queryByRole("searchbox")).toBeNull();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign out" })));
    expect(log).toContain("keel.signOut:global");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
  });

  it("shows 'sign-in server unreachable' on 503 and retries", async () => {
    auth.session = SESSION;
    status = () => err(503, "AUTH_UNAVAILABLE");
    render(<AuthGate callback={null} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Sign-in server unreachable");
    status = () => json({ groups: [], isolation: "container", version: "1" });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
    expect(await screen.findByRole("searchbox")).toBeTruthy();
  });

  it("goes back to sign-in when the API answers 401", async () => {
    auth.session = SESSION;
    status = () => err(401, "UNAUTHENTICATED");
    render(<AuthGate callback={null} />);
    expect(await screen.findByText("Your session ended. Sign in again.")).toBeTruthy();
    expect(log).toContain("keel.signOut:local");
  });

  it("sends a reset link that comes back to FileX", async () => {
    const reset = vi.spyOn(auth, "resetPasswordForEmail");
    render(<AuthGate callback={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Forgot password?" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ana@home.lan" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send reset link" })));
    expect(reset).toHaveBeenCalledWith("ana@home.lan", { redirectTo: `${window.location.origin}/` });
    expect(screen.getByRole("status").textContent).toContain("a reset link is on its way");
  });
});

describe("email links", () => {
  it("invite: sets a password, strips the fragment and lands in the catalog", async () => {
    window.history.replaceState(null, "", "/#access_token=at-1&refresh_token=r&expires_in=900&token_type=bearer&type=invite");
    const update = vi.spyOn(auth, "updateUser");
    const callback = readCallback();
    expect(window.location.hash).toBe(""); // stripped before anything renders (review L-4)
    render(<AuthGate callback={callback} />);
    expect(await screen.findByRole("heading", { name: "Set your password" })).toBeTruthy();
    expect(log).toContain("keel.setSession:at-1/r");

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "short" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Save password/ })));
    expect(screen.getByRole("alert").textContent).toContain("Use 12 to 72 characters.");
    expect(update).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a long enough phrase" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a long enough phrase" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Save password/ })));
    expect(update).toHaveBeenCalledWith({ password: "a long enough phrase" });
    expect(await screen.findByRole("searchbox")).toBeTruthy();
  });

  it("strips any token or error fragment in every mode and keeps only invite or recovery sessions", () => {
    const at = "access_token=other&refresh_token=r2&expires_in=900&token_type=bearer";
    for (const [fragment, expected] of [
      [`#${at}`, null], // no type: maybe someone else's session (login CSRF, review L-5)
      [`#${at}&type=magiclink`, null],
      [`#${at}&type=signup`, null],
      [`#${at}&type=recovery`, { type: "recovery", errorCode: null, tokens: { access_token: "other", refresh_token: "r2" } }],
      ["#access_token=x&type=invite", null], // no refresh token
      ["#error=access_denied&error_code=otp_expired&error_description=x", { type: null, errorCode: "otp_expired" }],
    ] as const) {
      window.history.replaceState(null, "", `/tools?q=1${fragment}`);
      expect(readCallback()).toEqual(expected);
      expect(window.location.hash).toBe("");
      expect(window.location.pathname + window.location.search).toBe("/tools?q=1");
    }
    window.history.replaceState(null, "", "/#section");
    expect(readCallback()).toBeNull();
    expect(window.location.hash).toBe("#section"); // other fragments are not ours to touch
  });

  describe("a link for another account while signed in (review final L-1)", () => {
    const eve = jwt({ sub: "u-eve", email: "eve@home.lan" });
    const link = { type: "recovery", errorCode: null, tokens: { access_token: eve, refresh_token: "r-eve" } };
    beforeEach(() => {
      auth.session = SESSION;
      vi.spyOn(auth, "setSession").mockImplementation(async (tokens) => {
        log.push(`keel.setSession:${tokens.refresh_token}`);
        auth.session = { access_token: tokens.access_token, user: { id: "u-eve", email: "eve@home.lan" } };
        return { data: { session: auth.session }, error: null };
      });
    });

    it("asks first and keeps the current session unless the user switches", async () => {
      render(<AuthGate callback={link} />);
      expect(await screen.findByRole("heading", { name: "Switch account?" })).toBeTruthy();
      expect(screen.getByText("ana@home.lan")).toBeTruthy();
      expect(screen.getByText("eve@home.lan")).toBeTruthy();
      expect(log.some((l) => l.startsWith("keel.setSession"))).toBe(false);

      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stay signed in" })));
      await screen.findByRole("searchbox");
      expect(log).toContain("GET /api/v1/status Bearer at-1");
      expect(log.some((l) => l.startsWith("keel.setSession"))).toBe(false);
      expect(auth.session?.user.email).toBe("ana@home.lan");
      expect(screen.getByText(/Signed in as/).textContent).toBe("Signed in as ana@home.lan");
    });

    it("switches only after the user confirms, and names the new account", async () => {
      render(<AuthGate callback={link} />);
      const swap = await screen.findByRole("button", { name: "Switch account" });
      await act(async () => fireEvent.click(swap));
      expect(log).toContain("keel.setSession:r-eve");
      expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeTruthy();
      expect(screen.getByText(/Signed in as/).textContent).toContain("Signed in as eve@home.lan");
    });

    it("an undecodable token counts as another account", async () => {
      render(<AuthGate callback={{ ...link, tokens: { access_token: "not-a-jwt", refresh_token: "r" } }} />);
      expect(await screen.findByRole("heading", { name: "Switch account?" })).toBeTruthy();
      expect(screen.getByText("a different account")).toBeTruthy();
    });

    it("a link for the account already signed in goes straight on", async () => {
      const own = jwt({ sub: "u-ana", email: "ana@home.lan" });
      render(<AuthGate callback={{ ...link, tokens: { access_token: own, refresh_token: "r-ana" } }} />);
      expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeTruthy();
      expect(log).toContain("keel.setSession:r-ana");
    });
  });

  it("tokenUser reads sub and email from the payload and survives garbage", () => {
    expect(tokenUser(jwt({ sub: "u-1", email: "zoë@home.lan" }))).toEqual({ id: "u-1", email: "zoë@home.lan" });
    expect(tokenUser("x")).toEqual({ id: null, email: null });
    expect(tokenUser("a.!!!.c")).toEqual({ id: null, email: null });
    expect(tokenUser(jwt({ sub: 7 }))).toEqual({ id: null, email: null });
  });

  it("an expired or used link says so on the sign-in page", async () => {
    render(<AuthGate callback={{ type: null, errorCode: "otp_expired" }} />);
    expect((await screen.findByRole("alert")).textContent).toContain("This link has expired or was already used.");
  });
});

describe("token mode", () => {
  it("asks for an fxt_ token, sends it as Bearer only and forgets it when refused", async () => {
    config = { ...KEEL, authMode: "TOKEN", keelUrl: null, keelAnonKey: null };
    render(<AuthGate callback={null} />);
    const field = await screen.findByLabelText("Access token");
    expect(screen.queryByLabelText("Email")).toBeNull();

    fireEvent.change(field, { target: { value: "hunter2" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect(screen.getByRole("alert").textContent).toContain("Access tokens start with fxt_.");

    const token = `fxt_${"a".repeat(43)}`;
    fireEvent.change(field, { target: { value: token } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    await screen.findByRole("searchbox");
    expect(log).toContain(`GET /api/v1/status Bearer ${token}`);
    expect(sessionStorage.getItem("filex-token")).toBe(token);

    status = () => err(401, "UNAUTHENTICATED");
    cleanup();
    render(<AuthGate callback={null} />);
    expect((await screen.findByRole("alert")).textContent).toContain("That token was not accepted.");
    await waitFor(() => expect(sessionStorage.getItem("filex-token")).toBeNull());
  });
});
