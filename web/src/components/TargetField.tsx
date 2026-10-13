// "Convert to" control for converter tools (ADR-0005 target amendment). Lists only the formats
// every chosen file can reach: radio cards for up to 4, a native select otherwise (design.md
// options mapping). Lossy targets say so in text, never by colour alone.
import { formatLabel, type FormatIndex, type Target, type TargetPlan } from "../catalog";
import { Alert } from "./Alert";
import { Icon } from "./Icon";

const LOSSY = "Some formatting may be lost.";

type Props = {
  plan: TargetPlan;
  formats: FormatIndex;
  value: string;
  error?: string;
  disabled?: boolean;
  onChange: (id: string) => void;
};

export function TargetField({ plan, formats, value, error, disabled, onChange }: Props) {
  if (plan.kind === "none") return null;
  if (plan.kind === "waiting") return <p className="help">Add files to see the formats they can be converted to.</p>;
  if (plan.kind === "auto") {
    return (
      <p className="help target-auto">
        Converts to <strong>{formatLabel(plan.target.id)}</strong>.{plan.target.lossy && ` ${LOSSY}`}
      </p>
    );
  }
  if (plan.kind === "conflict") {
    const names = plan.formats.map(formatLabel);
    const list = names.length === 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
    return (
      <Alert title="These files have no format in common.">
        <p>
          {list} files cannot be converted to the same format. Remove some files or convert them in separate jobs.
        </p>
      </Alert>
    );
  }

  const id = "opt-target";
  const errId = `${id}-err`;
  const helpId = `${id}-help`;
  const picked = plan.targets.find((t) => t.id === value);
  const help = `Only formats every chosen file can become are listed.${picked?.lossy ? ` ${formatLabel(picked.id)}: ${LOSSY.toLowerCase()}` : ""}`;
  const describedBy = [error && errId, helpId].filter(Boolean).join(" ");
  const errLine = error && (
    <span className="err" id={errId}>
      <Icon name="alert" size={16} />
      {error}
    </span>
  );
  const helpLine = (
    <span className="help" id={helpId}>
      {help}
    </span>
  );
  const describe = (t: Target) => [longTitle(t.id, formats), t.lossy ? LOSSY : ""].filter(Boolean).join(" ");

  if (plan.targets.length <= 4) {
    return (
      <fieldset className="field" aria-describedby={describedBy} disabled={disabled}>
        <legend>Convert to</legend>
        <div className="opts">
          {plan.targets.map((t) => {
            const d = describe(t);
            return (
              <label className="opt" key={t.id}>
                <input
                  type="radio"
                  name={id}
                  value={t.id}
                  checked={value === t.id}
                  required
                  onChange={() => onChange(t.id)}
                />
                <span className="t">
                  {formatLabel(t.id)}
                  <Icon name="check" size={16} />
                </span>
                {d && <span className="d">{d}</span>}
              </label>
            );
          })}
        </div>
        {errLine}
        {helpLine}
      </fieldset>
    );
  }

  return (
    <div className="field">
      <label className="label" htmlFor={id}>
        Convert to
      </label>
      <select
        className="input"
        id={id}
        required
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        value={picked ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="" disabled>
          Choose a format
        </option>
        {plan.targets.map((t) => (
          <option key={t.id} value={t.id}>
            {optionText(t, formats)}
          </option>
        ))}
      </select>
      {errLine}
      {helpLine}
    </div>
  );
}

/** "Word document" when the format list has a title that says more than the short label. */
function longTitle(id: string, formats: FormatIndex): string {
  const title = formats.get(id)?.title;
  return title && title.toUpperCase() !== formatLabel(id) ? title : "";
}

function optionText(t: Target, formats: FormatIndex): string {
  const title = longTitle(t.id, formats);
  return `${formatLabel(t.id)}${title ? ` (${title})` : ""}${t.lossy ? " · formatting may be lost" : ""}`;
}
