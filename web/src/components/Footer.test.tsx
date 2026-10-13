import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Footer, LICENSE_URL, NOTICES_URL, SOURCE_URL } from "./Footer";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("Footer", () => {
  it("is a contentinfo landmark with the version, licence, source and notices links", () => {
    render(<Footer version="1.4.0" />);
    const footer = screen.getByRole("contentinfo");
    expect(footer.textContent).toBe("FileX 1.4.0·AGPL-3.0·Source code·Third-party notices");
    const f = within(footer);
    expect(f.getByRole("link", { name: "Source code" }).getAttribute("href")).toBe(SOURCE_URL);
    expect(f.getByRole("link", { name: "AGPL-3.0 licence" }).getAttribute("href")).toBe(LICENSE_URL);
    expect(f.getByRole("link", { name: "Third-party notices" }).getAttribute("href")).toBe(NOTICES_URL);
  });

  it("points at the public repository by default", () => {
    expect(SOURCE_URL).toBe("https://github.com/niklan1110/filex-app");
    expect(LICENSE_URL).toBe("https://github.com/niklan1110/filex-app/blob/main/LICENSE");
  });

  it("leaves the version out while it is unknown", () => {
    render(<Footer />);
    expect(screen.getByRole("contentinfo").textContent).toMatch(/^FileX·AGPL-3\.0/);
  });

  it("uses VITE_FILEX_SOURCE_URL when a fork sets it", async () => {
    vi.stubEnv("VITE_FILEX_SOURCE_URL", "https://git.example.org/me/filex-fork");
    vi.resetModules();
    const m = await import("./Footer");
    expect(m.SOURCE_URL).toBe("https://git.example.org/me/filex-fork");
    expect(m.NOTICES_URL).toBe("https://git.example.org/me/filex-fork/blob/main/THIRD_PARTY_NOTICES.md");
  });
});
