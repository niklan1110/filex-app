export type ThemeChoice = "system" | "light" | "dark";

const KEY = "filex-theme";

export function readTheme(): ThemeChoice {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system"; // storage blocked
  }
}

export function applyTheme(t: ThemeChoice): void {
  const root = document.documentElement;
  if (t === "system") delete root.dataset.theme;
  else root.dataset.theme = t;
}

export function saveTheme(t: ThemeChoice): void {
  applyTheme(t);
  try {
    localStorage.setItem(KEY, t);
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}
