// The conversion route as a vertical rail (design.md "Convert page"): a format sheet per stop, the
// step between two stops with its converter and worker, a dashed rail and an amber badge on lossy
// steps. The last stop is the folded ink sheet. Screen readers get one sentence per step.
import { Fragment } from "react";
import type { ConvertPlan } from "../api/client";
import { formatLabel, formatTitle, type FormatIndex } from "../catalog";
import { formatsLabel, stepsText, workerName } from "../convert";
import { Icon } from "./Icon";

export const LOSSY_TEXT = "Some formatting may be lost";

function titles(ids: string[], formats: FormatIndex): string {
  return ids.map((id) => formatTitle(id, formats)).join(" and ");
}

export function RoutePreview({ plan, formats }: { plan: ConvertPlan; formats: FormatIndex }) {
  const first = plan.steps[0]?.src ?? plan.sources;
  const stops = [first, ...plan.steps.map((s) => [s.dst])];
  const workers = [...new Set(plan.steps.map((s) => s.group))].map(workerName);
  const lossy = plan.steps.filter((s) => s.lossy).length;

  return (
    <div className="route">
      <p className="route-line">
        {stops.map((ids, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <>
                <span className="route-arrow" aria-hidden="true">
                  {" → "}
                </span>
                <span className="sr-only"> to </span>
              </>
            )}
            <span className="route-stop">{formatsLabel(ids)}</span>
          </Fragment>
        ))}
      </p>
      <p className="fsize route-meta">
        {stepsText(plan.steps.length)} · {workers.join(", ")}
        {lossy > 0 && ` · ${lossy === 1 ? "1 lossy step" : `${lossy} lossy steps`}`}
      </p>

      <ol className="rail" aria-label="Steps">
        {plan.steps.map((s) => (
          <li key={s.index} data-lossy={s.lossy || undefined}>
            <span className="node" aria-hidden="true">
              {formatsLabel(s.src)}
            </span>
            <span className="node-title" aria-hidden="true">
              {titles(s.src, formats)}
            </span>
            <div className="hop">
              <span className="sr-only">
                Step {s.index + 1}: {titles(s.src, formats)} to {formatTitle(s.dst, formats)} with {s.title}, on the{" "}
                {workerName(s.group)}.
              </span>
              <span className="hop-n" aria-hidden="true">
                Step {s.index + 1}
              </span>
              <span className="hop-t" aria-hidden="true">
                {s.title}
              </span>
              <span className="hop-w" aria-hidden="true">
                {workerName(s.group)}
              </span>
              {s.lossy && (
                <span className="badge badge-warn">
                  <Icon name="alert" size={14} />
                  {LOSSY_TEXT}
                </span>
              )}
            </div>
          </li>
        ))}
      </ol>
      <div className="rail-end" aria-hidden="true">
        <span className="node node-end folded">{formatLabel(plan.target)}</span>
        <span className="node-title">{formatTitle(plan.target, formats)}</span>
      </div>
    </div>
  );
}
