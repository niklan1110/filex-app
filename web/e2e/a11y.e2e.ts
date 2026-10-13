// axe scans (WCAG 2.x A/AA) on the catalog, a tool page and a result, in both themes for the
// catalog. Serious and critical violations fail the test; every violation is printed and attached
// so minor ones are visible too.
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";
import { JOB_TIMEOUT, corpus, expect, openTool, requireStack, runButton, test, waitForResult } from "./helpers";

test.beforeAll(async ({ baseURL }) => requireStack(baseURL));

async function scan(page: Page, info: TestInfo, label: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const lines = result.violations.map(
    (v) => `[${v.impact}] ${v.id}: ${v.help}\n${v.nodes.map((n) => `    ${n.target.join(" ")}`).join("\n")}`,
  );
  await info.attach(`axe-${label}.txt`, { body: lines.join("\n") || "no violations", contentType: "text/plain" });
  if (lines.length) console.log(`axe ${label}:\n${lines.join("\n")}`);
  const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), label).toEqual([]);
}

function theme(page: Page, t: "light" | "dark") {
  return page.addInitScript((value) => {
    try {
      localStorage.setItem("filex-theme", value);
    } catch {
      // storage blocked
    }
  }, t);
}

for (const t of ["light", "dark"] as const) {
  test(`catalog (${t})`, async ({ page }, info) => {
    await theme(page, t);
    await page.goto("/");
    await expect(page.locator("a.tile").first()).toBeVisible();
    await scan(page, info, `catalog-${t}`);
  });
}

test("tool page with files and a refused file", async ({ page }, info) => {
  await theme(page, "light");
  await openTool(page, "pdf.merge");
  await page.locator("#choose-files").setInputFiles([
    corpus("pdf/text_1p.pdf"),
    corpus("pdf/text_10p.pdf"),
    corpus("office/report.docx"),
  ]);
  await expect(page.locator("ol.files li")).toHaveCount(2);
  await expect(page.locator(".alert.refused")).toBeVisible();
  await scan(page, info, "tool-merge");
});

test("tool page with a target picker and options", async ({ page }, info) => {
  await theme(page, "light");
  await openTool(page, "pdf.watermark");
  await scan(page, info, "tool-watermark");
});

test("result card", async ({ page }, info) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await theme(page, "light");
  await openTool(page, "pdf.split");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/text_10p.pdf"));
  await page.locator("#opt-every").fill("5");
  await runButton(page).click();
  await waitForResult(page);
  await scan(page, info, "result-split");
  // the jobIds fixture deletes the job
});

test("compress result with outcome and before/after (light, dark)", async ({ page }, info) => {
  test.setTimeout(JOB_TIMEOUT + 60_000);
  await theme(page, "light");
  await openTool(page, "pdf.compress");
  await page.locator("#choose-files").setInputFiles(corpus("pdf/scanned_3p.pdf"));
  await runButton(page).click();
  await waitForResult(page);
  await expect(page.getByRole("img", { name: /after compression, / }).first()).toBeVisible({ timeout: 30_000 });
  await scan(page, info, "result-compress-light");
  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // Buttons fade their colours over 150 ms; scan the settled theme, not a mid-transition frame.
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));
  await scan(page, info, "result-compress-dark");
  // the jobIds fixture deletes the job
});

test("My jobs", async ({ page }, info) => {
  await theme(page, "light");
  await page.goto("/jobs");
  await expect(page.getByRole("heading", { name: "My jobs", level: 1 })).toBeVisible();
  await expect(page.locator("p.state[role=status]")).toHaveCount(0); // loaded
  await scan(page, info, "my-jobs");
});
