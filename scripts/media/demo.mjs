// Record docs/media/demo.webp from a running stack in local mode (FILEX_AUTH=none):
// compress a scan, then show a convert route. Needs `npm --prefix web ci` (for Playwright) and ffmpeg.
//
//   node scripts/media/demo.mjs <out-dir>          # frames + list.txt; deletes its job afterwards
//   cd <out-dir> && ffmpeg -f concat -safe 0 -i list.txt -vf "fps=12,scale=1120:-1:flags=lanczos" \
//     -c:v libwebp_anim -lossless 0 -q:v 88 -preset text -compression_level 6 -loop 0 -an demo.webp
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const { chromium } = createRequire(path.join(ROOT, "web", "package.json"))("playwright");
const BASE = process.env.FILEX_URL ?? "http://127.0.0.1:8080";
const CORPUS = path.join(ROOT, "tests", "corpus");
const outDir = path.resolve(process.argv[2] ?? "demo-out");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// A visible pointer, since headless video has none.
const CURSOR = () => {
  const add = () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.innerHTML =
      '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l15 9-6.5 1.5L16 20l-3 1.5-3.5-7.5L4 19z" fill="#111316" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: "fixed", left: "0", top: "0", zIndex: 2147483647, pointerEvents: "none", transform: "translate(640px,400px)" });
    document.documentElement.appendChild(c);
    // Hide the "open mode" warning banner so the demo shows only the tools.
    const st = document.createElement("style");
    st.textContent = ".banner[role=note]{display:none}";
    document.head.appendChild(st);
    window.addEventListener("mousemove", (e) => (c.style.transform = `translate(${e.clientX - 3}px,${e.clientY - 2}px)`), true);
    window.addEventListener("dragover", (e) => (c.style.transform = `translate(${e.clientX - 3}px,${e.clientY - 2}px)`), true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add);
  else add();
};

async function moveTo(page, locator, steps = 25) {
  const b = await locator.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps });
}

// Fake an OS file drag: dragenter, a few dragovers along a path, then drop, all with real bytes.
async function dragFile(page, file, mime, from, to) {
  const b64 = readFileSync(`${CORPUS}/${file}`).toString("base64");
  const name = file.split("/").pop();
  const dt = await page.evaluateHandle(
    ([b64, name, mime]) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const d = new DataTransfer();
      d.items.add(new File([bytes], name, { type: mime }));
      return d;
    },
    [b64, name, mime],
  );
  const fire = (type, x, y) =>
    page.evaluate(
      ([type, x, y, d]) => {
        const target = document.elementFromPoint(x, y) ?? document.body;
        target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: d }));
      },
      [type, x, y, dt],
    );
  await fire("dragenter", from.x, from.y);
  const n = 18;
  for (let i = 1; i <= n; i++) {
    const x = from.x + ((to.x - from.x) * i) / n;
    const y = from.y + ((to.y - from.y) * i) / n;
    await page.mouse.move(x, y);
    await fire("dragover", x, y);
    await wait(40);
  }
  await wait(500);
  await fire("drop", to.x, to.y);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 1,
  bypassCSP: true,
  colorScheme: "light",
});
await ctx.addInitScript(CURSOR);
const page = await ctx.newPage();
const jobs = [];
const frames = [];
page.on("response", async (res) => {
  if (res.request().method() === "POST" && /\/api\/v1\/(tools\/[^/]+|convert)\/jobs$/.test(res.url()) && res.ok()) {
    const body = await res.json().catch(() => null);
    if (body?.id) jobs.push(body.id);
  }
});

try {
  // 1. Catalog, search for compress.
  await page.goto(BASE + "/");
  await page.locator("input[type=search]").waitFor();
  // High-quality frames via the DevTools screencast (the built-in video is low bitrate).
  mkdirSync(`${outDir}/frames`, { recursive: true });
  const cdp = await ctx.newCDPSession(page);
  let n = 0;
  cdp.on("Page.screencastFrame", async (f) => {
    const name = `f${String(n++).padStart(5, "0")}.png`;
    writeFileSync(`${outDir}/frames/${name}`, Buffer.from(f.data, "base64"));
    frames.push([name, f.metadata.timestamp]);
    await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "png", maxWidth: 1280, maxHeight: 720, everyNthFrame: 1 });
  await wait(1200);
  const search = page.locator("input[type=search]");
  await moveTo(page, search);
  await search.click();
  await search.pressSequentially("compress", { delay: 90 });
  await wait(700);
  const tile = page.getByRole("link", { name: /Compress PDF/ }).first();
  await moveTo(page, tile);
  await wait(250);
  await tile.click();
  await page.locator("#choose-files").waitFor({ state: "attached" });
  await wait(900);

  // 2. Drop a scan onto the page, compress it.
  await dragFile(page, "pdf/scanned_3p.pdf", "application/pdf", { x: 1150, y: 120 }, { x: 640, y: 420 });
  await wait(1100);
  const run = page.locator(".submit-bar button[type=submit]");
  await moveTo(page, run);
  await wait(300);
  await run.click();
  await page.locator(".result h3.job-title").waitFor({ timeout: 120_000 });
  await wait(600);
  await page.mouse.move(1240, 700, { steps: 10 });
  await page.locator(".result").scrollIntoViewIfNeeded();
  await wait(2800);

  // 3. Convert: drop a Word file, see the route before running.
  const nav = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Convert" });
  await moveTo(page, nav);
  await nav.click();
  await page.getByRole("heading", { name: "Convert", level: 1 }).waitFor();
  await wait(500);
  const dark = page.getByText("Dark", { exact: true });
  await moveTo(page, dark, 20);
  await dark.click();
  await wait(900);
  await dragFile(page, "office/report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", { x: 1150, y: 140 }, { x: 640, y: 420 });
  await page.locator(".from-list").waitFor();
  await wait(900);
  for (const fmt of [/^PDF/, /^CBZ/]) {
    const r = page.getByRole("radio", { name: fmt });
    await r.scrollIntoViewIfNeeded();
    await moveTo(page, r, 20);
    await r.check();
    await page.locator(".route").waitFor();
    await wait(1900);
  }
  await wait(800);
  frames.push(["end", Date.now() / 1000]);
  // ffmpeg concat list with real durations.
  let list = "";
  for (let i = 0; i < frames.length - 1; i++) {
    list += `file 'frames/${frames[i][0]}'
duration ${(frames[i + 1][1] - frames[i][1]).toFixed(3)}
`;
  }
  list += `file 'frames/${frames[frames.length - 2][0]}'
`;
  writeFileSync(`${outDir}/list.txt`, list);
} finally {
  await ctx.close();
  for (const id of jobs) await fetch(`${BASE}/api/v1/jobs/${id}`, { method: "DELETE" }).catch(() => {});
  await browser.close();
  console.log("jobs cleaned:", jobs.length);
}
