import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthGate } from "./auth/AuthGate";
import { readCallback } from "./auth/keel";
import "./styles/tokens.css";
import "./styles/app.css";
import { applyTheme, readTheme } from "./theme";

// The strict CSP allows no inline script, so the saved theme is applied here, before render.
applyTheme(readTheme());

// First thing, in every auth mode: read an email link's fragment (invite, reset, or its error)
// and strip it from the address bar and history, so no token outlives this line (review L-4).
const callback = readCallback();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthGate callback={callback} />
  </StrictMode>,
);
