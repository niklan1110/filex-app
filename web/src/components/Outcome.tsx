// The size outcome of a compression job (spec 9.8, design.md "Result"): "−62%" at poster size,
// "8.4 MB → 3.2 MB", two size bars, and the target-size verdict. Sizes come from the job record
// (inputs[].size, RESULT outputs[].size), summed over every file of a batch.
import type { CSSProperties } from "react";
import type { Job } from "../api/client";
import { formatBytes } from "../messages";
import { Icon } from "./Icon";

/** Tools whose point is a smaller file; they get the outcome block. */
export const SIZE_TOOLS = new Set(["pdf.compress"]);

export type Target = { reached: boolean; label: string | null; note: string | null };

export type Outcome = {
  before: number;
  after: number;
  files: number;
  /** Whole percent saved, at least 1 when the result is smaller at all; 0 when it is not. */
  saved: number;
  target: Target | null;
};

const MINUS = "−";

/** "4 MB" from the submitted targetMb option, else from TARGET_NOT_REACHED's text, else null. */
function targetLabel(options: Record<string, unknown>, message: string | undefined): string | null {
  const mb = Number(options.targetMb);
  if (options.targetMb != null && options.targetMb !== "" && Number.isFinite(mb) && mb > 0) return `${mb} MB`;
  const m = message?.match(/target\D{0,24}?(\d+(?:[.,]\d+)?)\s*(KB|MB|GB)\b/i);
  return m ? `${m[1].replace(",", ".")} ${m[2].toUpperCase()}` : null;
}

export function outcomeOf(job: Job, options: Record<string, unknown> = {}): Outcome | null {
  const outs = job.outputs.filter((o) => o.role === "RESULT");
  if (!SIZE_TOOLS.has(job.tool) || job.inputs.length === 0 || outs.length === 0) return null;
  const before = job.inputs.reduce((n, i) => n + i.size, 0);
  const after = outs.reduce((n, o) => n + o.size, 0);
  const saved = after < before ? Math.max(1, Math.round((1 - after / before) * 100)) : 0;
  const missed = job.warnings.find((w) => w.code === "TARGET_NOT_REACHED");
  const label = targetLabel(options, missed?.message);
  const target = missed
    ? { reached: false, label, note: label ? null : missed.message }
    : label
      ? { reached: true, label, note: null }
      : null;
  return { before, after, files: job.inputs.length, saved, target };
}

export function TargetBadge({ target }: { target: Target | null }) {
  if (!target) return null;
  return target.reached ? (
    <span className="badge badge-ok">
      <Icon name="check" size={14} />
      Target reached
    </span>
  ) : (
    <span className="badge badge-warn">
      <Icon name="alert" size={14} />
      Best we could do
    </span>
  );
}

export function OutcomeBlock({ outcome }: { outcome: Outcome }) {
  const { before, after, files, saved, target } = outcome;
  const b = formatBytes(before);
  const a = formatBytes(after);
  const across = files > 1 ? ` across ${files} files` : "";

  let line: string;
  if (target && !target.reached) {
    line = target.label ? `Best we could do: ${a} (target ${target.label}).` : `Best we could do: ${a}. ${target.note ?? ""}`;
  } else if (saved === 0) {
    line = files > 1 ? "The files were already compact, so their size was kept." : "The file was already compact, so its size was kept.";
  } else {
    line = `${formatBytes(before - after)} saved${across}` + (target?.label ? `, under the ${target.label} target` : "");
  }

  const verdict =
    target && target.label ? (target.reached ? `, target under ${target.label} reached` : `, target ${target.label} not reached`) : "";
  const label =
    saved === 0
      ? `Original ${b}, now ${a}, not smaller${verdict}.`
      : `Original ${b}, compressed ${a}, ${saved} percent smaller${verdict}.`;

  return (
    <div className="res-grid">
      {saved > 0 ? (
        <p className="outcome" aria-hidden="true">
          {MINUS}
          {saved}%
        </p>
      ) : (
        <p className="statement outcome-kept">Already as small as it gets</p>
      )}
      <div>
        <p className="figure" aria-hidden="true">
          {b} → {a}
        </p>
        <p className="muted outcome-line">{line}</p>
        <div className="sizes" role="img" aria-label={label}>
          <div className="size-row" aria-hidden="true">
            <span>Original</span>
            <span className="size-bar" />
            <span className="v">{b}</span>
          </div>
          <div className="size-row" aria-hidden="true">
            <span>Now</span>
            <span>
              <span className="size-bar now" style={{ "--w": Math.min(1, after / Math.max(1, before)) } as CSSProperties} />
            </span>
            <span className="v">{a}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
