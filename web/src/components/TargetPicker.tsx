// "Convert to" for the Convert page: one radio group of format cards, split by family (PDF,
// documents, ebooks, images). Each card says its step count and, in words, when a step is lossy.
import type { ConvertTarget } from "../api/client";
import { formatLabel, type FormatIndex } from "../catalog";
import { byFamily, stepsText } from "../convert";
import { Icon } from "./Icon";

type Props = {
  targets: ConvertTarget[];
  formats: FormatIndex;
  value: string;
  disabled?: boolean;
  onChange: (id: string) => void;
};

export function TargetPicker({ targets, formats, value, disabled, onChange }: Props) {
  return (
    <fieldset className="tpick" disabled={disabled} aria-describedby="to-help">
      <legend className="sr-only">Convert to</legend>
      <p className="help" id="to-help">
        Only formats these files can reach on this server are listed.
      </p>
      {byFamily(targets).map((g) => (
        <div key={g.id} className="tfam" role="group" aria-labelledby={`fam-${g.id}`}>
          <h3 className="eyebrow" id={`fam-${g.id}`}>
            {g.title}
          </h3>
          <div className="tcards">
            {g.targets.map((t) => {
              const title = formats.get(t.format)?.title;
              return (
                <label className="opt tcard" key={t.format}>
                  <input
                    type="radio"
                    name="convert-to"
                    value={t.format}
                    checked={value === t.format}
                    onChange={() => onChange(t.format)}
                  />
                  <span className="t">
                    {formatLabel(t.format)}
                    <Icon name="check" size={16} />
                  </span>
                  {title && title.toUpperCase() !== formatLabel(t.format) && <span className="d">{title}</span>}
                  <span className="m">{stepsText(t.steps)}</span>
                  {t.lossy && (
                    <span className="m lossy">
                      <Icon name="alert" size={14} />
                      May lose formatting
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        </div>
      ))}
    </fieldset>
  );
}
