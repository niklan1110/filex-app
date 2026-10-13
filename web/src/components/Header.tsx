import { useState } from "react";
import { Link } from "../router";
import { readTheme, saveTheme, type ThemeChoice } from "../theme";
import { Icon } from "./Icon";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

// Sign out sits next to the email on desktop and at the end of the nav row on mobile.
// "Signed in as <email>" shows at every width, so a session swapped by a link is visible (review final L-1).
export function Header({ path, email, onSignOut }: { path: string; email: string | null; onSignOut?: () => void }) {
  const [theme, setTheme] = useState(readTheme);
  const onTools = path === "/" || path.startsWith("/tools/");

  return (
    <header className="app-header">
      <div className="inner">
        <Link className="brand" to="/">
          <span className="mark folded" aria-hidden="true">
            <Icon name="logo" size={16} />
          </span>
          FileX
        </Link>
        <nav className="nav" aria-label="Main">
          <Link to="/" aria-current={onTools ? "page" : undefined}>
            Tools
          </Link>
          <Link to="/convert" aria-current={path === "/convert" ? "page" : undefined}>
            Convert
          </Link>
          <Link to="/jobs" aria-current={path === "/jobs" ? "page" : undefined}>
            My jobs
          </Link>
          {onSignOut && (
            <button type="button" className="signout-m" onClick={onSignOut}>
              Sign out
            </button>
          )}
        </nav>
        <div className="hdr-right">
          <fieldset className="segmented">
            <legend className="sr-only">Theme</legend>
            {THEMES.map((t) => (
              <label key={t.value}>
                <input
                  type="radio"
                  name="theme"
                  value={t.value}
                  checked={theme === t.value}
                  onChange={() => {
                    saveTheme(t.value);
                    setTheme(t.value);
                  }}
                />
                {t.label}
              </label>
            ))}
          </fieldset>
          {email && (
            <span className="user">
              Signed in as <span className="mono">{email}</span>
            </span>
          )}
          {onSignOut && (
            <button type="button" className="btn-text signout-d" onClick={onSignOut}>
              Sign out
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
