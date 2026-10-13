// E2E against a running FileX (docker compose up, FILEX_AUTH=none). See tests/e2e/README.md.
// Light on purpose: one worker, headless Chromium, no video, traces and screenshots on failure only.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts", // not *.spec.ts, so vitest (npm test) never picks these up
  outputDir: "./test-results",
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["junit", { outputFile: "test-results/junit.xml" }]] : "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.FILEX_URL ?? "http://127.0.0.1:8080",
    headless: true,
    video: "off",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    acceptDownloads: true,
    actionTimeout: 15_000,
  },
  projects: [{ name: "chromium" }],
});
