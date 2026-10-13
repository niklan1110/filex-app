// Sign-in screens for KEEL and TOKEN mode, with the API and Keel faked by route interception.
// No real Keel is contacted: every request to the fake Keel host is aborted.
import type { Page, Route } from "@playwright/test";
import { expect, expectNoHorizontalScroll, shot, test } from "./helpers";

const KEEL = "https://keel.e2e.invalid";
const LIMITS = { maxUploadMb: 200, maxFiles: 50, retentionMin: 1440 };

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}
const apiError = (code: string, message: string) => ({ error: { code, message, details: {} } });

type StatusAnswer = { status: number; body: unknown };

/** Fake GET /config and GET /status; everything else under /api/v1 answers 404. */
async function fakeApi(page: Page, authMode: "KEEL" | "TOKEN", status: StatusAnswer) {
  await page.route(`${KEEL}/**`, (r) => r.abort());
  await page.route("**/api/v1/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/config") {
      return json(route, 200, {
        authMode,
        keelUrl: authMode === "KEEL" ? KEEL : null,
        keelAnonKey: authMode === "KEEL" ? "anon-e2e" : null,
        limits: LIMITS,
        version: "e2e",
      });
    }
    if (url.pathname === "/api/v1/status") return json(route, status.status, status.body);
    if (url.pathname === "/api/v1/session") return route.fulfill({ status: 204 });
    return json(route, 404, apiError("NOT_FOUND", "Not faked in this test."));
  });
}

/** A stored supabase-js session, so KEEL mode goes straight to the GET /status check. */
async function seedKeelSession(page: Page) {
  const key = `sb-${new URL(KEEL).hostname.split(".")[0]}-auth-token`;
  const now = Math.floor(Date.now() / 1000);
  const session = {
    access_token: "e2e-access-token",
    refresh_token: "e2e-refresh-token",
    token_type: "bearer",
    expires_in: 3600,
    expires_at: now + 3600,
    user: { id: "00000000-0000-4000-8000-000000000001", email: "jane@example.test", aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {}, created_at: new Date().toISOString() },
  };
  await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [key, JSON.stringify(session)] as const);
}

const OK = { status: 200, body: { status: "ok" } };

test.describe("Keel mode", () => {
  test("no session shows the email and password form", async ({ page }) => {
    await fakeApi(page, "KEEL", OK);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in", level: 1 })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Forgot password?" })).toBeVisible();
    // No open-mode banner and no tool list behind the form.
    await expect(page.getByText("Open mode: sign-in is off.")).toHaveCount(0);
    await shot(page, "signin-keel");
  });

  test("401 from the API ends the session and shows sign-in", async ({ page }) => {
    await seedKeelSession(page);
    await fakeApi(page, "KEEL", { status: 401, body: apiError("UNAUTHENTICATED", "Sign in first.") });
    await page.goto("/");
    await expect(page.getByText("Your session ended. Sign in again.")).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
  });

  test("403 USER_NOT_ALLOWED shows the not-allowed screen", async ({ page }) => {
    await seedKeelSession(page);
    await fakeApi(page, "KEEL", { status: 403, body: apiError("USER_NOT_ALLOWED", "Not on the list.") });
    await page.goto("/");
    await expect(page.getByRole("alert")).toContainText("This account is not on the FileX list. Ask your admin to add it.");
    await expect(page.getByText("jane@example.test")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await shot(page, "signin-not-allowed");
  });

  test("503 AUTH_UNAVAILABLE shows the unreachable screen with Try again", async ({ page }) => {
    await seedKeelSession(page);
    let answer: StatusAnswer = { status: 503, body: apiError("AUTH_UNAVAILABLE", "Keel did not answer.") };
    await page.route(`${KEEL}/**`, (r) => r.abort());
    await page.route("**/api/v1/**", (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === "/api/v1/config") {
        return json(route, 200, { authMode: "KEEL", keelUrl: KEEL, keelAnonKey: "anon-e2e", limits: LIMITS, version: "e2e" });
      }
      if (p === "/api/v1/status") return json(route, answer.status, answer.body);
      return json(route, 200, []); // tools, formats, jobs once the retry succeeds
    });
    await page.goto("/");
    await expect(page.getByRole("alert")).toContainText("Sign-in server unreachable");
    // Keel back: Try again lets the user in.
    answer = OK;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  });

  test("a reset link for another account asks first and keeps the session on 'Stay signed in'", async ({ page }) => {
    // Review final L-1: anyone on the list can send a link carrying their own tokens and `type=recovery`.
    await seedKeelSession(page);
    await fakeApi(page, "KEEL", OK);
    await page.route("**/api/v1/{tools,formats}", (r) => json(r, 200, []));
    const claims = Buffer.from(JSON.stringify({ sub: "00000000-0000-4000-8000-000000000002", email: "eve@example.test" })).toString("base64url");
    await page.goto(`/#access_token=eyJhbGciOiJIUzI1NiJ9.${claims}.sig&refresh_token=eve-refresh&expires_in=3600&token_type=bearer&type=recovery`);
    await expect(page.getByRole("heading", { name: "Switch account?", level: 1 })).toBeVisible();
    await expect(page.getByText("jane@example.test")).toBeVisible();
    await expect(page.getByText("eve@example.test")).toBeVisible();
    expect(new URL(page.url()).hash).toBe(""); // tokens stripped from the address bar
    await shot(page, "signin-switch-account");

    await page.getByRole("button", { name: "Stay signed in" }).click();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.locator(".user")).toHaveText("Signed in as jane@example.test");
    // Still visible on a phone: the header gives the account its own row.
    await page.setViewportSize({ width: 360, height: 740 });
    await expect(page.locator(".user")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(1); // only the nav-row one
    await expectNoHorizontalScroll(page);
  });
});

test.describe("Token mode", () => {
  test("shows a token field instead of email", async ({ page }) => {
    await fakeApi(page, "TOKEN", OK);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in", level: 1 })).toBeVisible();
    await expect(page.getByLabel("Email")).toHaveCount(0);
    await expect(page.getByLabel(/token/i)).toBeVisible();
    await shot(page, "signin-token");
  });

  test("a rejected token (401) says so and stays on the token screen", async ({ page }) => {
    await fakeApi(page, "TOKEN", { status: 401, body: apiError("UNAUTHENTICATED", "Bad token.") });
    await page.goto("/");
    await page.getByLabel(/token/i).fill("fxt_" + "a".repeat(43));
    await page.getByRole("button", { name: /sign in|continue/i }).click();
    await expect(page.getByText("That token was not accepted.")).toBeVisible();
    await expect(page.getByLabel(/token/i)).toBeVisible();
  });

  test("an accepted token opens the app with no open-mode banner", async ({ page }) => {
    await fakeApi(page, "TOKEN", OK);
    await page.route("**/api/v1/{tools,formats}", (r) => json(r, 200, []));
    await page.goto("/");
    await page.getByLabel(/token/i).fill("fxt_" + "b".repeat(43));
    await page.getByRole("button", { name: /sign in|continue/i }).click();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByText("Open mode: sign-in is off.")).toHaveCount(0);
  });
});
