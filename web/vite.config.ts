import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    // The icon sprite must stay a same-origin file: <use> refuses data: URLs, and the strict
    // CSP has no data: source. Nothing is inlined.
    assetsInlineLimit: 0,
    // The polyfill is the only thing Vite could inject inline; every target browser has
    // native modulepreload.
    modulePreload: { polyfill: false },
  },
  // The PDF.js worker (src/preview/renderPage.ts, `?worker&url`) is an ES module; PDF.js starts
  // it as a module worker from a same-origin .js file, so the CSP needs no blob: or eval.
  worker: { format: "es" },
  server: {
    // `npm run dev` against an API on :8080. Keel comes from GET /config; VITE_KEEL_URL and
    // VITE_KEEL_ANON_KEY in web/.env.local are a dev-server-only fallback (never in a build).
    proxy: { "/api": "http://localhost:8080" },
  },
  test: {
    environment: "jsdom",
  },
});
