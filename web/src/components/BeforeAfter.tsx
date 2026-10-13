// Before/after of page 1 (spec 9.7, 9.8; design.md "Result", plan task 26). The original is the
// file the user picked, still in the browser; the result comes through a fresh signed link. Both
// are rendered by PDF.js to PNG and shown in <img> (never iframe/object/embed). Side by side from
// 40rem, one frame with a divider and a range input below. Starts when the block nears the
// viewport, stops (and frees the images) when it unmounts.
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { fetchOutput, type JobOutput } from "../api/client";
import { formatBytes } from "../messages";
import { MAX_PREVIEW_BYTES, renderFirstPage } from "../preview/renderPage";

/** Tools with a before/after preview of a PDF result. */
export const PREVIEW_TOOLS = new Set(["pdf.compress", "scan.clean"]);

type State =
  | { kind: "idle" }
  | { kind: "ready"; before: string; after: string }
  | { kind: "off"; why: string };

type Props = { original: File; jobId: string; output: JobOutput; verb: string };

export function BeforeAfter({ original, jobId, output, verb }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [pos, setPos] = useState(50);
  const tooBig = Math.max(original.size, output.size) > MAX_PREVIEW_BYTES;

  useEffect(() => {
    if (tooBig) return;
    const ac = new AbortController();
    const urls: string[] = [];
    let io: IntersectionObserver | undefined;

    const start = async () => {
      try {
        // One at a time: half the peak memory, and the page stays responsive on weak hardware.
        const before = await renderFirstPage(await original.arrayBuffer(), ac.signal);
        urls.push(URL.createObjectURL(before));
        const after = await renderFirstPage(await fetchOutput(jobId, output.index, ac.signal), ac.signal);
        urls.push(URL.createObjectURL(after));
        if (!ac.signal.aborted) setState({ kind: "ready", before: urls[0], after: urls[1] });
      } catch {
        if (!ac.signal.aborted) setState({ kind: "off", why: "The preview could not be drawn. The result itself is fine." });
      }
    };

    if (typeof IntersectionObserver === "undefined" || !box.current) void start();
    else {
      io = new IntersectionObserver(
        (entries) => {
          if (!entries.some((e) => e.isIntersecting)) return;
          io?.disconnect();
          void start();
        },
        { rootMargin: "200px" },
      );
      io.observe(box.current);
    }
    return () => {
      ac.abort();
      io?.disconnect();
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [original, jobId, output.index, tooBig]);

  function drag(e: PointerEvent<HTMLDivElement>) {
    if (e.type === "pointerdown") e.currentTarget.setPointerCapture(e.pointerId);
    else if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width > 0) setPos(Math.round(Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100))));
  }

  const beforeAlt = `Page 1 of ${original.name} before ${verb}, ${formatBytes(original.size)}`;
  const afterAlt = `Page 1 of ${output.name} after ${verb}, ${formatBytes(output.size)}`;
  const off = tooBig ? `No preview for files over ${formatBytes(MAX_PREVIEW_BYTES)}.` : state.kind === "off" ? state.why : null;

  return (
    <div className="ba" role="group" aria-label="Page 1, before and after" ref={box}>
      {off ? (
        <p className="muted ba-off">{off}</p>
      ) : state.kind !== "ready" ? (
        <div className="ba-side ba-wait" aria-busy="true">
          <p className="sr-only">Drawing page 1 before and after…</p>
          <span className="page-img ph" aria-hidden="true" />
          <span className="page-img ph" aria-hidden="true" />
        </div>
      ) : (
        <>
          <div className="ba-side">
            <figure>
              <img className="page-img" src={state.before} alt={beforeAlt} />
              <figcaption>
                <span>Before</span>
                <span className="muted">page 1, {formatBytes(original.size)}</span>
              </figcaption>
            </figure>
            <figure>
              <img className="page-img" src={state.after} alt={afterAlt} />
              <figcaption>
                <span>After</span>
                <span className="muted">page 1, {formatBytes(output.size)}</span>
              </figcaption>
            </figure>
          </div>
          <div className="ba-slide" style={{ "--pos": `${pos}%` } as CSSProperties}>
            <div className="cmp" onPointerDown={drag} onPointerMove={drag}>
              <img className="page-img" src={state.before} alt={`${beforeAlt}, left of the divider`} />
              <img className="after" src={state.after} alt={`${afterAlt}, right of the divider`} />
              <span className="handle" aria-hidden="true" />
            </div>
            <div className="cap" aria-hidden="true">
              <span>Before</span>
              <span>After</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={pos}
              onChange={(e) => setPos(Number(e.target.value))}
              aria-label="Compare position, before on the left, after on the right"
              aria-valuetext={`${pos}% before, ${100 - pos}% after`}
            />
          </div>
        </>
      )}
    </div>
  );
}
