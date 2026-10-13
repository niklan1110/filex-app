import { useEffect, useRef } from "react";
import { paths, type Format } from "./api/client";
import { useApi } from "./api/useApi";
import { formatIndex } from "./catalog";
import { Header } from "./components/Header";
import { Catalog } from "./pages/Catalog";
import { ConvertPage } from "./pages/Convert";
import { MyJobs } from "./pages/MyJobs";
import { ToolPage } from "./pages/ToolPage";
import { Link, usePath } from "./router";

// No banner for FILEX_AUTH=none: the API allows it only on a loopback bind (ADR-0011), so the
// only person who can reach this page is someone on this computer.
type Props = {
  email?: string | null;
  onSignOut?: () => void;
};

export function App({ email = null, onSignOut }: Props) {
  const path = usePath();
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);
  const { data: formats } = useApi<Format[]>(paths.formats());
  const fmt = formatIndex(formats);

  // Route change: start at the top and move focus to the new page (not on first load).
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo(0, 0);
    mainRef.current?.focus();
  }, [path]);

  const toolId = /^\/tools\/([^/]+)$/.exec(path)?.[1];
  let page;
  if (path === "/") {
    page = <Catalog formats={fmt} />;
  } else if (path === "/convert") {
    page = <ConvertPage formats={fmt} />;
  } else if (path === "/jobs") {
    page = <MyJobs />;
  } else if (toolId) {
    page = <ToolPage key={toolId} id={decodeURIComponent(toolId)} formats={fmt} />;
  } else {
    page = (
      <>
        <h1 className="page-title">Page not found</h1>
        <p className="state">
          <Link to="/">See all tools</Link>
        </p>
      </>
    );
  }

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header path={path} email={email} onSignOut={onSignOut} />
      <main className="main" id="main" tabIndex={-1} ref={mainRef}>
        {page}
      </main>
    </>
  );
}
