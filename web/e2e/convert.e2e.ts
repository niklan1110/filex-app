// Convert page (spec 9.11, design.md "Convert page") against the live planner and runners:
// docx → pdf (office.to-pdf), md → epub (one markup.convert step), md → pdf (two steps across the
// ebook worker: markup.convert then ebook.convert), docx → txt (a lossy step), NO_ROUTE for a pair
// with no edges (epub → xlsx), popular conversions, axe light and dark, 360 px reflow, no CSP
// violations. Downloads are checked: PDF via pdf-lib, EPUB as a zip with its mimetype entry first.
//
// One case stays intercepted: NO_ROUTE *with a profile hint* ("Enable the ebook add-on"). It only
// happens when an add-on is off, and the owner's stack runs every profile, so the planner's answer
// is faked for that test alone (marked below). Every other request is real; the jobIds fixture
// deletes every job created.
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";
import {
  JOB_TIMEOUT,
  corpus,
  download,
  expect,
  expectNoHorizontalScroll,
  pdfHasText,
  pdfPageCount,
  requireStack,
  settle,
  shot,
  test,
  zipEntries,
} from "./helpers";

async function scan(page: Page, info: TestInfo, label: string) {
  await settle(page);
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const lines = res.violations.map((v) => `[${v.impact}] ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  await info.attach(`axe-${label}.txt`, { body: lines.join("\n") || "no violations", contentType: "text/plain" });
  const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), label).toEqual([]);
}

function theme(page: Page, value: "light" | "dark") {
  return page.addInitScript((v) => {
    try {
      localStorage.setItem("filex-theme", v);
    } catch {
      // storage blocked
    }
  }, value);
}

function watchCsp(page: Page): string[] {
  const csp: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text());
  });
  return csp;
}

/** Wait for the convert job's result heading; on failure, say what the card says. */
async function waitForConverted(page: Page, name: string, timeout = JOB_TIMEOUT) {
  const heading = page.locator(".result h3.job-title");
  const badge = page.locator(".job .badge").first();
  await expect(badge).toHaveText(/Done|Failed|Cancelled/, { timeout });
  if ((await badge.textContent())?.trim() !== "Done") {
    const why = await page.locator(".job [role=alert]").allTextContents();
    throw new Error(`The convert job did not succeed: ${why.join(" ")}`);
  }
  await expect(heading).toHaveText(`${name} is ready`);
  return heading;
}

const MD = Buffer.from("# Field notes\n\nThe quick brown fox jumps over the lazy dog.\n\n## Second\n\nHello from FileX.\n");

test.beforeAll(async ({ baseURL }) => requireStack(baseURL));

test("docx: detected format, lossy route preview, then docx → pdf runs for real and downloads", async ({ page }, info) => {
  test.setTimeout(JOB_TIMEOUT + 60_000);
  await theme(page, "light");
  const csp = watchCsp(page);

  await page.goto("/");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Convert" }).click();
  await expect(page).toHaveURL(/\/convert$/);
  await expect(page.getByRole("heading", { name: "Convert", level: 1 })).toBeVisible();

  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));
  await expect(page.locator(".from-list")).toContainText("DOCX");
  await expect(page.locator(".from-list")).toContainText("Word document");
  await expect(page.getByRole("radio", { name: /^PDF/ })).toBeAttached();

  // A lossy route: docx → txt is one markup.convert step marked lossy by the live planner.
  await page.getByRole("radio", { name: /^TXT/ }).check();
  await expect(page.locator(".route .badge-warn")).toHaveText("Some formatting may be lost");
  await expect(page.locator(".route-meta")).toContainText("1 lossy step");
  await expect(page.locator(".route-stop")).toHaveText(["DOCX", "TXT"]);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 360));
  await shot(page, "convert-route-preview");
  await scan(page, info, "convert-route-light");

  await page.getByRole("radio", { name: /^PDF/ }).check();
  await expect(page.locator(".route .hop-t")).toHaveText(["Office to PDF"]);
  await expect(page.locator(".route .hop-w")).toHaveText(["office add-on"]);
  await expect(page.locator(".route .badge-warn")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "convert-light");

  await page.getByRole("button", { name: "Convert 1 file to PDF" }).click();
  await expect(page.getByRole("heading", { name: "Convert to PDF, 1 file" })).toBeVisible();
  const heading = await waitForConverted(page, "report.pdf");
  await expect(heading).toBeFocused();

  const { dl, bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(dl.suggestedFilename()).toBe("report.pdf");
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
  expect(await pdfHasText(bytes)).toBe(true);
  await page.getByRole("button", { name: "Delete now" }).click();
  await expect(heading).toHaveText("Deleted from the server");
  expect(csp, "no CSP violations").toEqual([]);
});

test("md → epub from a catalog-style link, in dark mode: one markup.convert step, valid EPUB", async ({ page }, info) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await theme(page, "dark");
  await page.goto("/convert?from=md&to=epub");
  await expect(page.getByLabel("From")).toHaveValue("md");
  await expect(page.locator(".route-stop")).toHaveText(["MD", "EPUB"]);
  await expect(page.locator(".route .hop-t")).toHaveText(["Convert Markdown, HTML and documents"]);
  await expect(page.locator(".route .hop-w")).toHaveText(["ebook add-on"]);

  await page.locator("#choose-files").setInputFiles({ name: "notes.md", mimeType: "text/markdown", buffer: MD });
  await expect(page.locator(".from-list")).toContainText("MD");
  await expect(page.getByRole("radio", { name: /^EPUB/ })).toBeChecked();
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "convert-dark");
  await scan(page, info, "convert-dark");

  await page.getByRole("button", { name: "Convert 1 file to EPUB" }).click();
  await waitForConverted(page, "notes.epub");
  const { dl, bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(dl.suggestedFilename()).toBe("notes.epub");
  const entries = zipEntries(bytes);
  expect(entries[0].name).toBe("mimetype");
  expect(entries[0].data.toString("latin1")).toBe("application/epub+zip");
  expect(entries.map((e) => e.name)).toContain("META-INF/container.xml");
  const xhtml = entries.filter((e) => /\.x?html$/.test(e.name)).map((e) => e.data.toString("utf8")).join("\n");
  expect(xhtml).toContain("quick brown fox");
  await page.getByRole("button", { name: "Delete now" }).click();
});

test("md → pdf is a two-step route (markup.convert then ebook.convert); My jobs lists it", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT * 2);
  await theme(page, "light");
  await page.goto("/convert?from=md&to=pdf");
  await expect(page.locator(".route-stop")).toHaveText(["MD", "EPUB", "PDF"]);
  await expect(page.locator(".route .hop-t")).toHaveText(["Convert Markdown, HTML and documents", "Convert ebooks"]);
  await expect(page.locator(".route-meta")).toContainText("2 steps");

  await page.locator("#choose-files").setInputFiles({ name: "notes.md", mimeType: "text/markdown", buffer: MD });
  const created = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/v1/convert/jobs"));
  await page.getByRole("button", { name: "Convert 1 file to PDF" }).click();
  const job = await (await created).json();
  expect(job.steps.map((s: { tool: string }) => s.tool)).toEqual(["markup.convert", "ebook.convert"]);
  await waitForConverted(page, "notes.pdf", JOB_TIMEOUT * 1.5);
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
  expect(await pdfPageCount(bytes)).toBeGreaterThanOrEqual(1);
  expect(await pdfHasText(bytes)).toBe(true);

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "My jobs" }).click();
  await expect(page.getByRole("heading", { name: "My jobs", level: 1 })).toBeVisible();
  const newest = page.locator("ul.jobs > li").first();
  await expect(newest).toContainText(/Convert/);
  await expect(newest.locator(".badge")).toHaveText("Done");
  await shot(page, "my-jobs-convert");
  // The jobIds fixture deletes this job.
});

test("docx → txt: the lossy step runs, the text comes through", async ({ page }) => {
  test.setTimeout(JOB_TIMEOUT + 30_000);
  await theme(page, "light");
  await page.goto("/convert?to=txt");
  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));
  await expect(page.getByRole("radio", { name: /^TXT/ })).toBeChecked();
  await expect(page.locator(".route .badge-warn")).toBeVisible();
  await page.getByRole("button", { name: "Convert 1 file to TXT" }).click();
  await waitForConverted(page, "report.txt");
  const { bytes } = await download(page, page.getByRole("button", { name: /^Download \(/ }));
  const text = bytes.toString("utf8");
  expect(text).toContain("FileX DOCX sample");
  expect(text).toContain("Sphinx");
  await page.getByRole("button", { name: "Delete now" }).click();
});

test("NO_ROUTE (live): epub → xlsx has no edges; 360 px has no sideways scroll; axe", async ({ page, request }, info) => {
  const plan = await request.get("/api/v1/convert/plan?from=epub&to=xlsx");
  expect(plan.status()).toBe(422);
  expect((await plan.json()).error.code).toBe("NO_ROUTE");

  await theme(page, "light");
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/convert?from=epub&to=xlsx");
  const alert = page.locator(".route-panel .alert");
  await expect(alert).toContainText("No route from EPUB to XLSX right now.");
  await expect(alert).toContainText("Pick another format.");
  await expect(alert).toContainText("[NO_ROUTE]");
  await expectNoHorizontalScroll(page);
  await page.locator(".route-panel").evaluate((el) => el.scrollIntoView({ block: "start" }));
  await shot(page, "convert-no-route");
  await scan(page, info, "convert-no-route-mobile");

  // A real route on the same small screen.
  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));
  await page.getByRole("radio", { name: /^PDF/ }).check();
  await expect(page.locator(".route-stop")).toHaveText(["DOCX", "PDF"]);
  await expect(page.getByRole("button", { name: "Convert 1 file to PDF" })).toBeEnabled();
  await expectNoHorizontalScroll(page);
  await page.locator(".conv-to").scrollIntoViewIfNeeded();
  await shot(page, "convert-mobile");
});

// INTERCEPTED: only the planner's answer for this one pair is faked, because every add-on is on
// in the owner's stack, so a NO_ROUTE that names a profile cannot happen there.
test("NO_ROUTE naming an add-on (intercepted planner answer)", async ({ page }) => {
  await theme(page, "light");
  await page.route("**/api/v1/convert/plan?*", (r) =>
    r.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "NO_ROUTE", message: "FileX cannot convert docx to epub.", details: { sources: ["docx"], target: "epub", profiles: ["ebook"] } },
      }),
    }),
  );
  await page.goto("/convert");
  await page.locator("#choose-files").setInputFiles(corpus("office/report.docx"));
  await page.getByRole("radio", { name: /^EPUB/ }).check();
  const alert = page.locator(".route-panel .alert");
  await expect(alert).toContainText("No route from DOCX to EPUB right now.");
  await expect(alert).toContainText("Enable the ebook add-on: add ebook to COMPOSE_PROFILES in .env, then run docker compose up -d.");
  await expect(page.getByRole("button", { name: /^Convert/ })).toBeDisabled();
});

test("catalog: popular conversions link to the Convert page with the pair chosen", async ({ page }, info) => {
  await theme(page, "light");
  await page.goto("/");
  const word = page.getByRole("link", { name: "Word to PDF" });
  await expect(word).toBeVisible();
  await expect(page.getByRole("link", { name: "Markdown to EPUB" })).toBeVisible();
  await page.locator(".popular").evaluate((el) => el.closest("section")?.scrollIntoView({ block: "start" }));
  await shot(page, "catalog-popular-conversions");
  await scan(page, info, "catalog-popular");
  await word.click();
  await expect(page).toHaveURL(/\/convert\?from=docx&to=pdf$/);
  await expect(page.getByLabel("From")).toHaveValue("docx");
  await expect(page.getByRole("radio", { name: /^PDF/ })).toBeChecked();
  await expect(page.locator(".route-stop")).toHaveText(["DOCX", "PDF"]);
});
