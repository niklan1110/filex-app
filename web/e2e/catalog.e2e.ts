// Catalog, search, filters, theme and narrow screens against the live stack (FILEX_AUTH=none).
import { corpus, expect, expectNoHorizontalScroll, openTool, requireStack, shot, test } from "./helpers";

test.beforeAll(async ({ baseURL }) => requireStack(baseURL));

test.beforeEach(async ({ page }) => {
  // Start every test in the light theme so screenshots and colour checks are stable.
  await page.addInitScript(() => {
    try {
      if (!sessionStorage.getItem("e2e-theme-set")) {
        localStorage.setItem("filex-theme", "light");
        sessionStorage.setItem("e2e-theme-set", "1");
      }
    } catch {
      // storage blocked: the app falls back to the system theme
    }
  });
});

test("catalog lists every tool from the API, grouped, with no open-mode banner", async ({ page, request }) => {
  const tools = (await (await request.get("/api/v1/tools")).json()) as { id: string; title: string }[];
  await page.goto("/");
  await expect(page.locator(".lede")).toContainText(`${tools.length} tools on this server`);
  await expect(page.locator("a.tile")).toHaveCount(tools.length);
  await expect(page.getByRole("link", { name: /Merge PDFs/ })).toBeVisible();
  await expect(page.getByText("Open mode: sign-in is off.")).toHaveCount(0);
  expect(await page.locator("section.band h2").count()).toBeGreaterThan(1);
  await shot(page, "catalog-light");
});

test("search by keyword: 'combine' finds Merge PDFs", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("searchbox", { name: "Search tools" }).fill("combine");
  await expect(page.getByRole("link", { name: /Merge PDFs/ })).toBeVisible();
  await expect(page.locator(".lede [aria-live]")).toHaveText(/^\d+ tools? found\.$/);
  await expect(page.getByRole("link", { name: /Compress PDF/ })).toHaveCount(0);
});

test("search by format and by 'X to Y' phrase", async ({ page }) => {
  await page.goto("/");
  const search = page.getByRole("searchbox", { name: "Search tools" });
  await search.fill("docx");
  await expect(page.getByRole("link", { name: /Convert Office files/ })).toBeVisible();
  await search.fill("word to pdf");
  await expect(page.getByRole("link", { name: /Office to PDF/ })).toBeVisible();
});

test("'/' focuses search, but not while typing in it", async ({ page }) => {
  await page.goto("/");
  const search = page.getByRole("searchbox", { name: "Search tools" });
  await expect(page.locator("a.tile").first()).toBeVisible();
  await page.locator("body").click({ position: { x: 5, y: 300 } });
  await expect(search).not.toBeFocused();
  await page.keyboard.press("/");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue(""); // the slash was not typed
  await page.keyboard.type("a/b");
  await expect(search).toHaveValue("a/b");
});

test("no results: message and Clear search", async ({ page }) => {
  await page.goto("/");
  const search = page.getByRole("searchbox", { name: "Search tools" });
  await search.fill("zzqqxx");
  await expect(page.getByText("No tools match “zzqqxx”")).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(search).toHaveValue("");
  await expect(search).toBeFocused();
  await expect(page.locator("a.tile").first()).toBeVisible();
});

test("category chips filter the list", async ({ page }) => {
  await page.goto("/");
  const chips = page.getByRole("group", { name: "Filter by category" }).getByRole("button");
  await expect(chips.first()).toHaveAttribute("aria-pressed", "true"); // All
  const allTiles = await page.locator("a.tile").count();
  const second = chips.nth(1);
  await second.click();
  await expect(second).toHaveAttribute("aria-pressed", "true");
  await expect(chips.first()).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("section.band")).toHaveCount(1);
  expect(await page.locator("a.tile").count()).toBeLessThan(allTiles);
  await chips.first().click();
  await expect(page.locator("a.tile")).toHaveCount(allTiles);
});

test("light / dark theme switch changes colours and survives a reload", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("a.tile").first()).toBeVisible();
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const light = await bg();
  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect.poll(bg).not.toBe(light);
  await shot(page, "catalog-dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("radio", { name: "Dark" })).toBeChecked();
  await page.getByRole("radio", { name: "Light" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect.poll(bg).toBe(light);
});

test("an unknown tool id says so", async ({ page }) => {
  await page.goto("/tools/no.such-tool");
  await expect(page.getByRole("heading", { name: "No such tool" })).toBeVisible();
});

test("footer names the version and links to the licence and source (AGPL-3.0 section 13)", async ({ page, request }) => {
  const { version } = (await (await request.get("/api/v1/config")).json()) as { version: string };
  await page.goto("/");
  const footer = page.getByRole("contentinfo");
  await expect(footer).toContainText(`FileX ${version}`);
  const source = footer.getByRole("link", { name: "Source code" });
  await expect(source).toHaveAttribute("href", /^https?:\/\//);
  await expect(footer.getByRole("link", { name: "AGPL-3.0 licence" })).toHaveAttribute("href", /LICENSE$/);
  await expect(footer.getByRole("link", { name: "Third-party notices" })).toHaveAttribute("href", /THIRD_PARTY_NOTICES\.md$/);
  // Reachable by keyboard: focus lands on it and shows the ink ring.
  await footer.getByRole("link", { name: "AGPL-3.0 licence" }).focus();
  await page.keyboard.press("Tab");
  await expect(source).toBeFocused();
  expect(await source.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("solid");
});

test.describe("360 px wide", () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true });

  test("footer wraps without sideways scroll", async ({ page }) => {
    await page.goto("/");
    const footer = page.getByRole("contentinfo");
    await footer.scrollIntoViewIfNeeded();
    await expect(footer.getByRole("link", { name: "Source code" })).toBeVisible();
    const box = await footer.boundingBox();
    expect(box && box.x + box.width).toBeLessThanOrEqual(360);
    await expectNoHorizontalScroll(page);
  });

  test("catalog, tool page and My jobs do not scroll sideways", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("a.tile").first()).toBeVisible();
    await expectNoHorizontalScroll(page);
    await shot(page, "mobile-catalog");

    await openTool(page, "pdf.merge");
    await page.locator("#choose-files").setInputFiles([
      corpus("pdf/text_10p.pdf"),
      corpus("pdf/scanned_3p.pdf"),
    ]);
    await expect(page.locator("ol.files li")).toHaveCount(2);
    await expectNoHorizontalScroll(page);
    await shot(page, "mobile-tool");

    await page.goto("/jobs");
    await expect(page.getByRole("heading", { name: "My jobs" })).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
