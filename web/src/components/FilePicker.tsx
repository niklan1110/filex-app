// Drop zone, full-page drag overlay and the file list (design.md "Tool page"). Format, size and
// count are checked here before anything is uploaded; the server checks again by content.
import { useEffect, useEffectEvent, useRef, useState, type CSSProperties, type DragEvent } from "react";
import type { ToolDetail } from "../api/client";
import { acceptAttr, fileMatches, formatTitle, type FormatIndex } from "../catalog";
import { useLimits } from "../limits";
import { formatBytes } from "../messages";
import { Link } from "../router";
import { Icon } from "./Icon";

export type Item = { id: number; file: File };

let nextId = 1;

type Props = {
  tool: Pick<ToolDetail, "title" | "category" | "inputs" | "files">;
  /** Short words for what is accepted ("documents, ebooks, images and PDFs") when listing every format would be too long. */
  kinds?: string;
  formats: FormatIndex;
  items: Item[];
  onItems: (items: Item[]) => void;
  /** Bytes sent so far while uploading; null when not uploading. */
  uploaded: number | null;
  onCancelUpload: () => void;
  announce: (text: string) => void;
  locked: boolean;
};

export function FilePicker({ tool, kinds, formats, items, onItems, uploaded, onCancelUpload, announce, locked }: Props) {
  const [refused, setRefused] = useState<string[]>([]);
  const [over, setOver] = useState(false);
  const focusNext = useRef<string | null>(null);
  const limits = useLimits();
  const MAX_FILE_BYTES = limits.maxBytes;
  const { min, each } = tool.files;
  const max = Math.min(tool.files.max, limits.maxFiles);
  const names = tool.inputs.map((id) => formatTitle(id, formats));
  const onlyWhat = kinds ?? (names.length ? `${names.join(" or ")} files` : "supported files");
  const plural = names.length === 1 && !kinds ? `${names[0]}s` : "files";
  const accepted = kinds ? kinds[0].toUpperCase() + kinds.slice(1) : names.length ? `${names.join(", ")} only` : "Any supported file";
  const ordered = max > 1 && !each;
  const uploading = uploaded !== null;

  function add(list: File[]) {
    if (locked || list.length === 0) return;
    const ok: File[] = [];
    const why: string[] = [];
    for (const f of list) {
      if (!fileMatches(f.name, tool.inputs, formats)) why.push(`${f.name}: ${tool.title} takes ${onlyWhat} only.`);
      else if (f.size === 0) why.push(`${f.name} is empty.`);
      else if (f.size > MAX_FILE_BYTES)
        why.push(`${f.name} is ${formatBytes(f.size)}; the limit is ${formatBytes(MAX_FILE_BYTES)} per file.`);
      else ok.push(f);
    }
    const base = max === 1 ? [] : items;
    const room = Math.max(0, max - base.length);
    if (ok.length > room) {
      why.push(
        max === 1
          ? `${tool.title} takes one file; only ${ok[0].name} was kept.`
          : `${tool.title} takes at most ${max} files; ${ok.length - room} were not added.`,
      );
    }
    const added = ok.slice(0, room).map((file) => ({ id: nextId++, file }));
    setRefused(why);
    if (added.length) {
      const next = [...base, ...added];
      onItems(next);
      announce(
        `Added ${added.length} ${added.length === 1 ? "file" : "files"}. ${next.length} in the list.` +
          (why.length ? ` ${why.length} not added.` : ""),
      );
    }
  }

  // Whole-window drag and drop. Only drags that carry files open the overlay, so dragging a row
  // to reorder it does not.
  const addEvent = useEffectEvent(add);
  const announceEvent = useEffectEvent(announce);
  useEffect(() => {
    if (locked) return;
    let depth = 0;
    const hasFiles = (e: globalThis.DragEvent) => e.dataTransfer?.types.includes("Files") ?? false;
    const enter = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (++depth === 1) {
        setOver(true);
        announceEvent("Drop to add files. Press Escape to cancel.");
      }
    };
    const dragOver = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const leave = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setOver(false);
    };
    const drop = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setOver(false);
      addEvent(Array.from(e.dataTransfer?.files ?? []));
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        depth = 0;
        setOver(false);
      }
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", dragOver);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", dragOver);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
      window.removeEventListener("keydown", key);
      setOver(false);
    };
  }, [locked]);

  // Keep keyboard focus on the row a button acted on (or its neighbour) after the list changes.
  useEffect(() => {
    const id = focusNext.current;
    focusNext.current = null;
    if (id) document.getElementById(id)?.focus();
  }, [items]);

  function move(index: number, to: number) {
    const next = [...items];
    const [it] = next.splice(index, 1);
    next.splice(to, 0, it);
    const atEdge = to === 0 ? "down" : to === next.length - 1 ? "up" : null;
    const dir = to < index ? "up" : "down";
    focusNext.current = `row-${it.id}-${atEdge === null || atEdge === dir ? dir : atEdge}`;
    onItems(next);
    announce(`Moved ${it.file.name} to position ${to + 1} of ${next.length}.`);
  }

  function remove(index: number) {
    const it = items[index];
    const next = items.filter((_, i) => i !== index);
    const neighbour = next[index] ?? next[index - 1];
    focusNext.current = neighbour ? `row-${neighbour.id}-rm` : "choose-files";
    onItems(next);
    announce(`Removed ${it.file.name}. ${next.length} in the list.`);
  }

  // Row drag and drop (pointer alternative is the Move up / Move down buttons, WCAG 2.5.7).
  const [dragId, setDragId] = useState<number | null>(null);
  function rowDrop(e: DragEvent, to: number) {
    if (dragId === null) return;
    e.preventDefault();
    const from = items.findIndex((i) => i.id === dragId);
    setDragId(null);
    if (from >= 0 && from !== to) move(from, to);
  }

  // Per-row upload share, from the cumulative bytes sent (multipart overhead is ignored).
  const share: number[] = [];
  for (let i = 0, start = 0; i < items.length; start += items[i].file.size, i++) {
    const size = items[i].file.size;
    share.push(uploaded === null ? 0 : Math.min(1, Math.max(0, (uploaded - start) / size)));
  }

  return (
    <div>
      <div className="dropzone" data-active={over}>
        <span className="sheet folded" aria-hidden="true">
          <Icon name="drop" size={26} />
        </span>
        <p className="dz-line">Drop {plural} anywhere on this page</p>
        <label className="btn btn-secondary btn-strong">
          <Icon name="file" size={20} />
          Choose files
          <input
            id="choose-files"
            type="file"
            multiple={max > 1}
            accept={acceptAttr(tool.inputs, formats) || undefined}
            disabled={locked}
            onChange={(e) => {
              add(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </label>
        <p className="help">
          {accepted}
          {max > 1 ? `, up to ${max} files` : ", one file"}, {formatBytes(MAX_FILE_BYTES)} per file.
        </p>
      </div>

      {refused.length > 0 && (
        <div className="alert refused" role="alert">
          <Icon name="alert" size={18} />
          <div>
            <strong>{refused.length === 1 ? "A file was not added." : "Some files were not added."}</strong>
            <ul>
              {refused.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            <p>
              Need a different tool? <Link to="/">See all tools</Link>
            </p>
          </div>
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="list-head">
            <h2 className="section-title">Files</h2>
            <span className="fsize">
              {ordered ? "Used in this order · " : ""}
              {items.length} of {max === 1 ? 1 : `${min}–${max}`}
            </span>
          </div>
          <ol className="files">
            {items.map(({ id, file }, i) => {
              const pct = Math.round(share[i] * 100);
              return (
                <li
                  key={id}
                  draggable={ordered && !locked}
                  data-dragging={dragId === id || undefined}
                  onDragStart={(e) => {
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData("text/plain", file.name);
                    setDragId(id);
                  }}
                  onDragEnd={() => setDragId(null)}
                  onDragOver={(e) => dragId !== null && e.preventDefault()}
                  onDrop={(e) => rowDrop(e, i)}
                >
                  {ordered && (
                    <span className="grip" aria-hidden="true">
                      <Icon name="grip" size={20} />
                    </span>
                  )}
                  <span className="fname" title={file.name}>
                    {file.name}
                  </span>
                  <span className="fsize">{uploading ? `Uploading ${pct}%` : formatBytes(file.size)}</span>
                  <span className="row-btns">
                    {ordered && (
                      <>
                        <button
                          type="button"
                          className="icon-btn"
                          id={`row-${id}-up`}
                          aria-label={`Move ${file.name} up`}
                          disabled={locked || i === 0}
                          onClick={() => move(i, i - 1)}
                        >
                          <Icon name="up" size={18} />
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          id={`row-${id}-down`}
                          aria-label={`Move ${file.name} down`}
                          disabled={locked || i === items.length - 1}
                          onClick={() => move(i, i + 1)}
                        >
                          <Icon name="down" size={18} />
                        </button>
                      </>
                    )}
                    {uploading ? (
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label={`Cancel upload of ${file.name}`}
                        onClick={onCancelUpload}
                      >
                        <Icon name="x" size={18} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="icon-btn"
                        id={`row-${id}-rm`}
                        aria-label={`Remove ${file.name}`}
                        disabled={locked}
                        onClick={() => remove(i)}
                      >
                        <Icon name="trash" size={18} />
                      </button>
                    )}
                  </span>
                  {uploading && (
                    <span
                      className="row-bar"
                      role="progressbar"
                      aria-label={`Uploading ${file.name}`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={pct}
                    >
                      <span style={{ "--p": share[i] } as CSSProperties} />
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}

      <div className="overlay" data-open={over} aria-hidden="true">
        <div className={`drop-card field-surface folded cat-${tool.category}`}>
          <span className="sheet folded">
            <Icon name="drop" size={26} />
          </span>
          <p className="drop-big">Drop to add</p>
          <p className="drop-sub">{accepted}</p>
          <p className="drop-esc">
            <kbd>Esc</kbd> to cancel
          </p>
        </div>
      </div>
    </div>
  );
}
