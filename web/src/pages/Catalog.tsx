import { useEffect, useRef, useState } from "react";
import { paths, type ConvertSource, type ToolSummary } from "../api/client";
import { useApi } from "../api/useApi";
import { CATEGORIES, category, searchTools, toolIcon, type FormatIndex } from "../catalog";
import { Alert, loadFailedTitle } from "../components/Alert";
import { ConvertsHint } from "../components/ConvertsHint";
import { Icon } from "../components/Icon";
import { convertHref, pairText, popularPairs, shortName, type Pair } from "../convert";
import { Link } from "../router";

export function Catalog({ formats }: { formats: FormatIndex }) {
  const { data: tools, error, loading, retry } = useApi<ToolSummary[]>(paths.tools());
  // Older servers have no planner: a failed request just hides the popular conversions.
  const { data: reach } = useApi<ConvertSource[]>(paths.convertTargets());
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState("all");
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" focuses search, except while typing somewhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const all = tools ?? [];
  const found = searchTools(all, query, formats).filter((t) => cat === "all" || t.category === cat);
  const present = CATEGORIES.filter((c) => all.some((t) => t.category === c.id));
  const unknown = [...new Set(all.map((t) => t.category))]
    .filter((id) => !CATEGORIES.some((c) => c.id === id))
    .map(category);
  const groups = [...present, ...unknown]
    .map((c) => ({ ...c, tools: found.filter((t) => t.category === c.id) }))
    .filter((g) => g.tools.length > 0);
  const term = query.trim();
  const words = term.toLowerCase().split(/\s+/).filter((w) => w && w !== "to");
  const pairs = popularPairs(reach).filter((p) => {
    const text = pairText(p, formats);
    return words.every((w) => text.includes(w));
  });

  return (
    <>
      <h1 className="sr-only">Tools</h1>
      <form role="search" className="search" onSubmit={(e) => e.preventDefault()}>
        <Icon name="search" />
        <input
          ref={searchRef}
          type="search"
          aria-label="Search tools"
          aria-describedby="q-hint"
          placeholder="compress, word to pdf, docx…"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="hint" id="q-hint">
          <span className="sr-only">Shortcut: </span>
          <kbd>/</kbd>
        </span>
      </form>
      <p className="lede">
        {tools && `${all.length} ${all.length === 1 ? "tool" : "tools"} on this server. Files stay on your network. `}
        <span aria-live="polite">
          {tools && term ? `${found.length} ${found.length === 1 ? "tool" : "tools"} found.` : ""}
        </span>
      </p>

      {present.length + unknown.length > 1 && (
        <div className="chips" role="group" aria-label="Filter by category">
          <button type="button" className="chip" aria-pressed={cat === "all"} onClick={() => setCat("all")}>
            All
          </button>
          {[...present, ...unknown].map((c) => (
            <button
              key={c.id}
              type="button"
              className={`chip cat-${c.id}`}
              aria-pressed={cat === c.id}
              onClick={() => setCat(c.id)}
            >
              <span className="sw" aria-hidden="true" />
              {c.chip}
            </button>
          ))}
        </div>
      )}

      {loading && <p className="state" role="status">Loading tools…</p>}
      {error && (
        <div className="state">
          <Alert title={loadFailedTitle(error, "the tools")} error={error}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={retry}>
              Try again
            </button>
          </Alert>
        </div>
      )}
      {tools && all.length === 0 && (
        <p className="statement state">No tools are installed on this server.</p>
      )}

      {groups.length > 0 && (
        <div className="bands">
          {groups.map((g) => (
            <section key={g.id} className={`band field-surface folded cat-${g.id}`} aria-labelledby={`g-${g.id}`}>
              <div>
                <h2 id={`g-${g.id}`}>{g.band}</h2>
                <p className="count">
                  {g.tools.length} {g.tools.length === 1 ? "tool" : "tools"}
                </p>
              </div>
              <div className="band-body">
                <ul className="tiles">
                  {g.tools.map((t) => (
                    <li key={t.id}>
                      <ToolTile tool={t} />
                    </li>
                  ))}
                </ul>
                {g.id === "convert" && reach && (pairs.length > 0 || !term) && <Popular pairs={pairs} />}
              </div>
            </section>
          ))}
        </div>
      )}

      {all.length > 0 && found.length === 0 && (
        <div className="state">
          <p className="statement">No tools match “{term}”</p>
          <p className="muted">Try a format (pdf, docx) or a verb (shrink, join).</p>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setQuery("");
              setCat("all");
              searchRef.current?.focus();
            }}
          >
            Clear search
          </button>
        </div>
      )}
    </>
  );
}

function ToolTile({ tool }: { tool: ToolSummary }) {
  const offId = `off-${tool.id}`;
  return (
    <Link
      className={tool.available ? "tile" : "tile off"}
      to={`/tools/${encodeURIComponent(tool.id)}`}
      aria-describedby={tool.available ? undefined : offId}
    >
      <Icon name={toolIcon(tool)} />
      <span className="body">
        <span className="t">{tool.title}</span>
        <span className="s">{tool.summary}</span>
        <ConvertsHint tool={tool} />
        {tool.category !== "convert" && tool.inputs.length > 1 && (
          <span className="fmts">
            {tool.inputs.slice(0, 4).map((f) => (
              <span key={f} className="fmt">
                {f}
              </span>
            ))}
          </span>
        )}
        {!tool.available && (
          <span className="badge badge-warn" id={offId}>
            <Icon name="lock" size={14} />
            <span>
              Enable the <code>{tool.profile}</code> profile
            </span>
          </span>
        )}
      </span>
    </Link>
  );
}

/** "Word → PDF" links to the Convert page, only for pairs a running worker can do now. */
function Popular({ pairs }: { pairs: Pair[] }) {
  return (
    <div className="popular">
      <h3 className="eyebrow" id="popular-h">
        Popular conversions
      </h3>
      <ul className="pop" aria-labelledby="popular-h">
        {pairs.map((p) => (
          <li key={`${p.from}-${p.to}`}>
            <Link className="pop-link" to={convertHref(p.from, p.to)}>
              {shortName(p.from)}
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> to </span>
              {shortName(p.to)}
            </Link>
          </li>
        ))}
        <li>
          <Link className="pop-link pop-any" to="/convert">
            Any format
            <Icon name="arrow" size={16} />
          </Link>
        </li>
      </ul>
    </div>
  );
}
