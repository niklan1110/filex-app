// Page grid for pdf.organize (spec 9.4 "page-grid", 5.3; design.md options table; plan task 22).
// Thumbnails are PDF.js renders shown in <img> (never iframe/object/embed), drawn lazily as
// tiles near the viewport, two at a time, from one open document. Tiles are a multi-select
// listbox: arrows move focus, Space or a click selects, Ctrl/Alt + arrows move the selection,
// Delete deletes or restores it. The toolbar does the same with buttons (WCAG 2.5.7), plus
// "Move to page". Every change is announced. Off-screen tiles skip rendering
// (content-visibility), so 500+ pages stay smooth. The parent owns the entries (the plan).
import {
  memo,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { formatBytes } from "../messages";
import { openPdf, PdfPasswordError } from "../preview/renderPage";
import { THUMB_CSS_WIDTH, ThumbStore } from "../preview/thumbs";
import { Icon, type IconName } from "./Icon";
import {
  duplicate,
  initialEntries,
  moveBy,
  moveNextTo,
  moveTo,
  resultNumbers,
  setDeleted,
  turn,
  type Entry,
} from "./pagePlan";

/** Bigger files are not opened in the browser (the bytes sit in memory while arranging). */
export const MAX_GRID_BYTES = 100 * 1024 * 1024;

type Props = {
  file: File;
  entries: Entry[] | null;
  onEntries: (entries: Entry[]) => void;
  /** The plan's maximum length (the option's maxItems). */
  maxPages: number;
  error?: string;
  disabled?: boolean;
};

type Load = { kind: "loading" } | { kind: "ready"; store: ThumbStore } | { kind: "off"; why: string };

export function PageGrid({ file, entries, onEntries, maxPages, error, disabled }: Props) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [sel, setSel] = useState<ReadonlySet<number>>(new Set());
  const [focusKey, setFocusKey] = useState(1);
  const [mark, setMark] = useState<{ key: number; after: boolean } | null>(null);
  const [say, setSay] = useState("");
  const [to, setTo] = useState("");
  const anchor = useRef<number | null>(null);
  const drag = useRef<ReadonlySet<number> | null>(null);
  const refocus = useRef(false);
  const list = useRef<HTMLOListElement>(null);
  const tooBig = file.size > MAX_GRID_BYTES;

  const loaded = useEffectEvent((pages: number) => {
    setSel(new Set());
    setFocusKey(1);
    onEntries(initialEntries(pages));
  });

  useEffect(() => {
    if (tooBig) return;
    const ac = new AbortController();
    let store: ThumbStore | undefined;
    let close: (() => Promise<void>) | undefined;
    void (async () => {
      try {
        const doc = await openPdf(await file.arrayBuffer(), ac.signal);
        close = doc.destroy;
        if (ac.signal.aborted) return void doc.destroy();
        if (doc.numPages > maxPages) {
          setLoad({ kind: "off", why: `This PDF has ${doc.numPages} pages; the page grid takes up to ${maxPages}.` });
          return;
        }
        const width = Math.round(THUMB_CSS_WIDTH * Math.min(2, window.devicePixelRatio || 1));
        store = new ThumbStore((n, signal) => doc.render(n, width, signal, true));
        setLoad({ kind: "ready", store });
        loaded(doc.numPages);
      } catch (e) {
        if (ac.signal.aborted) return;
        const why =
          e instanceof PdfPasswordError
            ? "This PDF is password-protected. Remove the password first (Unlock PDF), then arrange its pages."
            : "The pages of this file could not be read in the browser. Check that it is a working PDF.";
        setLoad({ kind: "off", why });
      }
    })();
    return () => {
      ac.abort();
      store?.dispose();
      void close?.();
    };
  }, [file, maxPages, tooBig]);

  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    document.getElementById(`pg-${focusKey}`)?.focus();
  });

  if (tooBig || load.kind === "off") {
    const why = tooBig ? `Files over ${formatBytes(MAX_GRID_BYTES)} cannot be arranged in the browser.` : load.kind === "off" ? load.why : "";
    return (
      <section className="pg" aria-labelledby="pg-title">
        <h2 className="section-title" id="pg-title">Pages</h2>
        <p className="err" role="alert">
          <Icon name="alert" size={16} />
          {why}
        </p>
      </section>
    );
  }
  if (load.kind === "loading" || !entries) {
    return (
      <section className="pg" aria-labelledby="pg-title" aria-busy="true">
        <h2 className="section-title" id="pg-title">Pages</h2>
        <p className="muted" role="status">Reading the pages…</p>
      </section>
    );
  }

  const numbers = resultNumbers(entries);
  const kept = numbers.size;
  const describe = (keys: ReadonlySet<number>) => {
    if (keys.size !== 1) return `${keys.size} pages`;
    const e = entries.find((x) => keys.has(x.key))!;
    return `Page ${e.page} of the original`;
  };
  const where = (keys: ReadonlySet<number>, next: Entry[]) => {
    if (keys.size !== 1) return "";
    const n = resultNumbers(next).get([...keys][0]);
    return n ? `, now page ${n}` : "";
  };

  function commit(next: Entry[], message: string, focus = true) {
    onEntries(next);
    setSay(message);
    refocus.current = focus && document.activeElement?.closest(".pg-list") != null;
  }

  function move(keys: ReadonlySet<number>, dir: -1 | 1, steps = 1) {
    let next = entries!;
    for (let i = 0; i < steps; i++) next = moveBy(next, keys, dir);
    if (next.every((e, i) => e === entries![i])) return setSay(dir < 0 ? "Already at the start." : "Already at the end.");
    commit(next, `${describe(keys)} moved ${dir < 0 ? "earlier" : "later"}${where(keys, next)}.`);
  }

  function rotate(keys: ReadonlySet<number>, by: 90 | -90) {
    const next = turn(entries!, keys, by);
    const one = keys.size === 1 ? next.find((e) => keys.has(e.key))!.rotate : null;
    commit(next, `${describe(keys)} turned ${by > 0 ? "right" : "left"}${one !== null ? `, now ${one}°` : ""}.`);
  }

  function remove(keys: ReadonlySet<number>) {
    const restore = entries!.filter((e) => keys.has(e.key)).every((e) => e.deleted);
    const next = setDeleted(entries!, keys, !restore);
    const left = resultNumbers(next).size;
    commit(next, `${describe(keys)} ${restore ? "restored" : "deleted"}. ${left} ${left === 1 ? "page" : "pages"} in the result.`);
  }

  function copy(keys: ReadonlySet<number>) {
    const adding = entries!.filter((e) => keys.has(e.key) && !e.deleted).length;
    if (kept + adding > maxPages) return setSay(`The result can have at most ${maxPages} pages.`);
    commit(duplicate(entries!, keys).entries, `${describe(keys)} duplicated.`);
  }

  function moveToPage() {
    const n = Number(to);
    if (!sel.size) return setSay("Select pages first.");
    if (!Number.isInteger(n) || n < 1 || n > kept) return setSay(`Enter a page number from 1 to ${kept}.`);
    commit(moveTo(entries!, sel, n), `${describe(sel)} moved to page ${n}.`, false);
  }

  function select(key: number, how: "toggle" | "range") {
    const next = new Set(sel);
    if (how === "range" && anchor.current !== null) {
      const a = entries!.findIndex((e) => e.key === anchor.current);
      const b = entries!.findIndex((e) => e.key === key);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(entries![i].key);
    } else {
      if (next.has(key)) next.delete(key);
      else next.add(key);
      anchor.current = key;
    }
    setSel(next);
    setSay(`${next.has(key) ? "Selected" : "Not selected"}. ${next.size} selected.`);
  }

  const keyOf = (e: { target: EventTarget }) => Number((e.target as HTMLElement).closest<HTMLElement>("[data-key]")?.dataset.key);

  function onClick(e: MouseEvent) {
    const key = keyOf(e);
    if (disabled || !key) return;
    setFocusKey(key);
    select(key, e.shiftKey ? "range" : "toggle");
  }

  function onKeyDown(e: KeyboardEvent) {
    if (disabled) return;
    const i = Math.max(0, entries!.findIndex((x) => x.key === focusKey));
    const first = list.current?.children[0] as HTMLElement | undefined;
    let cols = 0;
    for (const c of list.current?.children ?? []) if ((c as HTMLElement).offsetTop === first?.offsetTop) cols++;
    cols = Math.max(1, cols);
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[e.key];
    const own: ReadonlySet<number> = sel.has(focusKey) ? sel : new Set([focusKey]);
    if (step !== undefined && (e.ctrlKey || e.altKey || e.metaKey)) {
      e.preventDefault();
      move(own, step < 0 ? -1 : 1, Math.abs(step));
    } else if (step !== undefined || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const j = e.key === "Home" ? 0 : e.key === "End" ? entries!.length - 1 : Math.min(entries!.length - 1, Math.max(0, i + step!));
      setFocusKey(entries![j].key);
      refocus.current = true;
      if (e.shiftKey) select(entries![j].key, "range");
    } else if (e.key === " ") {
      e.preventDefault();
      select(focusKey, "toggle");
    } else if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      setSel(new Set(entries!.map((x) => x.key)));
      setSay(`All ${entries!.length} selected.`);
    } else if (e.key === "Escape" && sel.size) {
      setSel(new Set());
      setSay("Selection cleared.");
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      remove(own);
    }
  }

  function onDragStart(e: DragEvent) {
    const key = keyOf(e);
    if (disabled || !key) return e.preventDefault();
    drag.current = sel.has(key) ? sel : new Set([key]);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", `${drag.current.size} pages`);
  }

  function onDragOver(e: DragEvent) {
    const key = keyOf(e);
    if (!drag.current || !key) return;
    e.preventDefault();
    const r = (e.target as HTMLElement).closest("[data-key]")!.getBoundingClientRect();
    const after = e.clientX > r.left + r.width / 2;
    if (mark?.key !== key || mark.after !== after) setMark({ key, after });
  }

  function onDrop(e: DragEvent) {
    const keys = drag.current;
    drag.current = null;
    setMark(null);
    if (!keys || !mark) return;
    e.preventDefault();
    const next = moveNextTo(entries!, keys, mark.key, mark.after);
    commit(next, `${describe(keys)} moved${where(keys, next)}.`, false);
  }

  const deletedCount = entries.length - kept;
  const allDeleted = sel.size > 0 && entries.filter((e) => sel.has(e.key)).every((e) => e.deleted);
  const inert = sel.size === 0 || disabled;
  const act = (run: () => void) => (disabled ? undefined : sel.size === 0 ? setSay("Select pages first.") : run());

  return (
    <section className="pg" aria-labelledby="pg-title">
      <div className="list-head">
        <h2 className="section-title" id="pg-title">Pages</h2>
        <span className="fsize">
          {kept} of {entries.length} kept{sel.size ? ` · ${sel.size} selected` : ""}
        </span>
      </div>
      <p className="help" id="pg-help">
        Click a page or press Space to select it. Move pages by dragging, with the buttons, or with Ctrl + arrow keys.
      </p>
      <div className="pg-bar" role="group" aria-label="Selected pages">
        <BarButton label="Earlier" icon="arrow" flip inert={inert} onPress={() => act(() => move(sel, -1))} />
        <BarButton label="Later" icon="arrow" inert={inert} onPress={() => act(() => move(sel, 1))} />
        <BarButton label="Turn left" icon="rotate" inert={inert} onPress={() => act(() => rotate(sel, -90))} />
        <BarButton label="Turn right" icon="rotate" flip inert={inert} onPress={() => act(() => rotate(sel, 90))} />
        <BarButton label="Duplicate" icon="copy" inert={inert} onPress={() => act(() => copy(sel))} />
        <BarButton
          label={allDeleted ? "Restore" : "Delete"}
          icon={allDeleted ? "undo" : "trash"}
          inert={inert}
          onPress={() => act(() => remove(sel))}
        />
        <span className="pg-to">
          <label htmlFor="pg-to">Move to page</label>
          <input
            id="pg-to"
            className="input mono"
            type="number"
            inputMode="numeric"
            min={1}
            max={kept}
            value={to}
            disabled={disabled}
            onChange={(e) => setTo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault(); // not the job form
              moveToPage();
            }}
          />
          <button type="button" className="btn btn-secondary btn-row" aria-disabled={inert || undefined} onClick={moveToPage}>
            Move
          </button>
        </span>
        <button
          type="button"
          className="btn-text"
          disabled={disabled}
          onClick={() => {
            const all = sel.size === entries.length;
            setSel(all ? new Set() : new Set(entries.map((x) => x.key)));
            setSay(all ? "Selection cleared." : `All ${entries.length} selected.`);
          }}
        >
          {sel.size === entries.length ? "Select none" : "Select all"}
        </button>
      </div>
      {error && (
        <p className="err" id="pg-err">
          <Icon name="alert" size={16} />
          {error}
        </p>
      )}
      <ol
        ref={list}
        className="pg-list"
        role="listbox"
        aria-multiselectable="true"
        aria-labelledby="pg-title"
        aria-describedby={[error && "pg-err", "pg-help"].filter(Boolean).join(" ")}
        aria-invalid={error ? true : undefined}
        aria-disabled={disabled || undefined}
        onClick={onClick}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          const key = keyOf(e);
          if (key) setFocusKey(key);
        }}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onDragEnd={() => {
          drag.current = null;
          setMark(null);
        }}
      >
        {entries.map((e) => (
          <Tile
            key={e.key}
            entry={e}
            no={numbers.get(e.key)}
            selected={sel.has(e.key)}
            focused={e.key === focusKey}
            mark={mark?.key === e.key ? (mark.after ? "after" : "before") : undefined}
            store={load.store}
            disabled={disabled}
          />
        ))}
      </ol>
      {deletedCount > 0 && (
        <p className="help">
          {deletedCount} {deletedCount === 1 ? "page is" : "pages are"} marked deleted and left out of the result.
        </p>
      )}
      <p className="sr-only" role="status">
        {say}
      </p>
    </section>
  );
}

type BarProps = { label: string; icon: IconName; flip?: boolean; inert?: boolean; onPress: () => void };

/** aria-disabled, not disabled, so focus stays on the button when the selection runs out. */
function BarButton({ label, icon, flip, inert, onPress }: BarProps) {
  return (
    <button type="button" className="btn btn-secondary btn-row" aria-disabled={inert || undefined} onClick={onPress}>
      <span className={flip ? "flip" : undefined}>
        <Icon name={icon} size={18} />
      </span>
      {label}
    </button>
  );
}

type TileProps = {
  entry: Entry;
  no: number | undefined;
  selected: boolean;
  focused: boolean;
  mark: "before" | "after" | undefined;
  store: ThumbStore;
  disabled?: boolean;
};

const Tile = memo(function Tile({ entry, no, selected, focused, mark, store, disabled }: TileProps) {
  const ref = useRef<HTMLLIElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const { key, page, rotate, deleted } = entry;

  useEffect(() => {
    let release: (() => void) | null = null;
    const ask = () => (release ??= store.request(page, setUrl));
    if (typeof IntersectionObserver === "undefined" || !ref.current) {
      ask();
      return () => release?.();
    }
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) ask();
        else {
          release?.();
          release = null;
        }
      },
      { rootMargin: "400px 0px" },
    );
    io.observe(ref.current);
    return () => {
      io.disconnect();
      release?.();
    };
  }, [store, page]);

  const turned = rotate ? `, turned ${rotate}° clockwise` : "";
  const label = deleted ? `Page ${page} of the original, deleted` : `Page ${no}, page ${page} of the original${turned}`;
  return (
    <li
      ref={ref}
      id={`pg-${key}`}
      data-key={key}
      className="pg-tile"
      role="option"
      aria-selected={selected}
      aria-label={label}
      tabIndex={focused ? 0 : -1}
      draggable={!disabled}
      data-deleted={deleted || undefined}
      data-mark={mark}
    >
      <span className="pg-frame" aria-hidden="true">
        {url ? (
          <img src={url} alt="" draggable={false} style={{ rotate: `${rotate}deg` }} />
        ) : (
          <span className="pg-ph" style={{ rotate: `${rotate}deg` }} />
        )}
        {selected && (
          <span className="pg-check">
            <Icon name="check" size={16} />
          </span>
        )}
        {rotate > 0 && <span className="pg-turn">{rotate}°</span>}
      </span>
      <span className="pg-cap" aria-hidden="true">
        {deleted ? (
          <span className="pg-no">
            <Icon name="trash" size={14} />
            Deleted
          </span>
        ) : (
          <span className="pg-no">{no}</span>
        )}
        <span className="pg-src">p.&nbsp;{page}</span>
      </span>
    </li>
  );
});
