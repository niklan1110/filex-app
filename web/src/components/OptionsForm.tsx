// Options form generated from a tool's optionsSchema (design.md "Options form: JSON Schema field
// → control"). Pure helpers (fieldsOf, initialValues, validate, buildOptions) are unit-tested.
// Values live in React state only; secret (writeOnly / password) values are never persisted.
import { useState, type ReactNode } from "react";
import { Icon } from "./Icon";

export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  format?: string;
  writeOnly?: boolean;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  multipleOf?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  definitions?: Record<string, JsonSchema>;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  "x-unit"?: string;
  "x-enum-titles"?: string[];
  "x-enum-descriptions"?: string[];
  "x-pattern-message"?: string;
  "x-widget"?: string;
  items?: JsonSchema;
  maxItems?: number;
};

export type Kind = "enum" | "boolean" | "number" | "integer" | "password" | "page-range" | "string";
export type Field = { name: string; kind: Kind; s: JsonSchema; required: boolean };
export type Values = Record<string, string | boolean>;
export type Errors = Record<string, string>;

/** Local refs only ("#/$defs/X"); anything else is ignored, never fetched. */
function resolve(s: JsonSchema, root: JsonSchema, depth = 0): JsonSchema {
  if (depth > 8) return s;
  const { $ref, allOf, anyOf, oneOf, ...own } = s;
  const m = $ref ? /^#\/(\$defs|definitions)\/([^/]+)$/.exec($ref) : null;
  if (m) return { ...resolve(root[m[1] as "$defs"]?.[m[2]] ?? {}, root, depth + 1), ...own };
  if (allOf?.length === 1) return { ...resolve(allOf[0], root, depth + 1), ...own };
  const branches = (anyOf ?? oneOf)?.filter((b) => b.type !== "null");
  if (branches?.length === 1) return { ...resolve(branches[0], root, depth + 1), ...own };
  return s;
}

function kindOf(s: JsonSchema): Kind | null {
  const type = Array.isArray(s.type) ? s.type.find((t) => t !== "null") : s.type;
  if (s.enum) return "enum";
  if (type === "boolean") return "boolean";
  if (type === "integer") return "integer";
  if (type === "number") return "number";
  if (type === "string") {
    if (s.writeOnly || s.format === "password") return "password";
    if (s.format === "page-range") return "page-range";
    return "string";
  }
  return null;
}

/** The fields the form can render, in schema order. Unknown shapes are skipped (server defaults). */
export function fieldsOf(schema: JsonSchema): Field[] {
  const req = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).flatMap(([name, raw]) => {
    const s = resolve(raw, schema);
    const kind = kindOf(s);
    return kind ? [{ name, kind, s, required: req.has(name) }] : [];
  });
}

/** The option a named widget edits instead of the form (e.g. "page-grid"), with its list bound. */
export function widgetField(schema: JsonSchema, widget: string): { name: string; maxItems: number } | null {
  for (const [name, s] of Object.entries(schema.properties ?? {})) {
    if (s["x-widget"] === widget) return { name, maxItems: s.maxItems ?? 2000 };
  }
  return null;
}

export function initialValues(fields: Field[]): Values {
  const v: Values = {};
  for (const f of fields) {
    if (f.kind === "boolean") v[f.name] = f.s.default === true;
    else if (f.kind === "password") v[f.name] = ""; // never pre-filled
    else v[f.name] = f.s.default === undefined || f.s.default === null ? "" : String(f.s.default);
  }
  return v;
}

const PAGE_PART = /^(\d+(-\d*)?|-\d+)$/;

export function validPageRange(text: string): boolean {
  const parts = text.replace(/\s+/g, "").split(",");
  return parts.every((p) => PAGE_PART.test(p) && !/(^|-)0+(\D|$)/.test(p));
}

function safeRegex(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern, "u");
  } catch {
    return null;
  }
}

export function validate(fields: Field[], values: Values): Errors {
  const errors: Errors = {};
  for (const { name, kind, s, required } of fields) {
    const v = values[name];
    if (typeof v === "boolean") continue;
    if (v === "" || v === undefined) {
      if (required) errors[name] = kind === "enum" ? "Choose one." : "Enter a value.";
      continue;
    }
    if (kind === "number" || kind === "integer") {
      const n = Number(v);
      const lo = s.minimum ?? s.exclusiveMinimum;
      const hi = s.maximum ?? s.exclusiveMaximum;
      const range = lo !== undefined && hi !== undefined ? ` Use a value from ${lo} to ${hi}.` : "";
      if (!Number.isFinite(n)) errors[name] = "Enter a number.";
      else if (kind === "integer" && !Number.isInteger(n)) errors[name] = `Enter a whole number.${range}`;
      else if (
        (s.minimum !== undefined && n < s.minimum) ||
        (s.exclusiveMinimum !== undefined && n <= s.exclusiveMinimum)
      )
        errors[name] = range ? range.trim() : `Use ${lo} or more.`;
      else if (
        (s.maximum !== undefined && n > s.maximum) ||
        (s.exclusiveMaximum !== undefined && n >= s.exclusiveMaximum)
      )
        errors[name] = range ? range.trim() : `Use ${hi} or less.`;
    } else if (kind === "page-range") {
      if (!validPageRange(v)) errors[name] = "Use page numbers and ranges, like 1-3, 5, 8-.";
    } else if (kind === "string" || kind === "password") {
      if (s.minLength !== undefined && v.length < s.minLength) errors[name] = `Use at least ${s.minLength} characters.`;
      else if (s.maxLength !== undefined && v.length > s.maxLength) errors[name] = `Use at most ${s.maxLength} characters.`;
      else if (s.pattern && safeRegex(s.pattern)?.test(v) === false)
        errors[name] = s["x-pattern-message"] ?? "This value is not in the expected form.";
    }
  }
  return errors;
}

/** The options JSON for the API: empty fields are left out so the server default applies. */
export function buildOptions(fields: Field[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const { name, kind, s } of fields) {
    const v = values[name];
    if (v === "" || v === undefined) continue;
    if (kind === "boolean") out[name] = v === true;
    else if (kind === "number" || kind === "integer") out[name] = Number(v);
    else if (kind === "enum") out[name] = s.enum?.find((e) => String(e) === v) ?? v;
    else out[name] = v;
  }
  return out;
}

function label(f: Field): string {
  return f.s.title ?? f.name;
}

type FormProps = {
  fields: Field[];
  values: Values;
  errors: Errors;
  disabled?: boolean;
  onChange: (name: string, value: string | boolean) => void;
};

export function OptionsForm({ fields, values, errors, disabled, onChange }: FormProps) {
  const [shown, setShown] = useState<Record<string, boolean>>({});
  return (
    <>
      {fields.map((f) => {
        const id = `opt-${f.name}`;
        const helpId = `${id}-help`;
        const errId = `${id}-err`;
        const err = errors[f.name];
        let help = f.s.description ?? "";
        if (f.s["x-unit"]) help = `${help} In ${f.s["x-unit"]}.`.trim();
        if (f.kind === "page-range") help = `${help} For example 1-3, 5, 8-.`.trim();
        if (f.kind === "password") help = `${help} Used for this job only and never stored.`.trim();
        const describedBy = [err && errId, help && helpId].filter(Boolean).join(" ") || undefined;
        const errLine = err && (
          <span className="err" id={errId}>
            <Icon name="alert" size={16} />
            {err}
          </span>
        );
        const helpLine = help && (
          <span className="help" id={helpId}>
            {help}
          </span>
        );
        const value = values[f.name];

        if (f.kind === "boolean") {
          return (
            <div className="field" key={f.name}>
              <label className="switch">
                <span>
                  <span className="label" id={`${id}-label`}>
                    {label(f)}
                  </span>
                  {helpLine}
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  id={id}
                  checked={value === true}
                  disabled={disabled}
                  aria-labelledby={`${id}-label`}
                  aria-describedby={describedBy}
                  onChange={(e) => onChange(f.name, e.target.checked)}
                />
                <span className="track" aria-hidden="true">
                  <span className="thumb" />
                </span>
              </label>
              {errLine}
            </div>
          );
        }

        if (f.kind === "enum" && (f.s.enum?.length ?? 0) <= 4) {
          const titles = f.s["x-enum-titles"];
          const descs = f.s["x-enum-descriptions"];
          return (
            <fieldset className="field" key={f.name} aria-describedby={describedBy} disabled={disabled}>
              <legend>{label(f)}</legend>
              <div className="opts">
                {f.s.enum!.map((opt, i) => (
                  <label className="opt" key={String(opt)}>
                    <input
                      type="radio"
                      name={id}
                      value={String(opt)}
                      checked={value === String(opt)}
                      required={f.required}
                      onChange={() => onChange(f.name, String(opt))}
                    />
                    <span className="t">
                      {titles?.[i] ?? String(opt)}
                      <Icon name="check" size={16} />
                    </span>
                    {descs?.[i] && <span className="d">{descs[i]}</span>}
                  </label>
                ))}
              </div>
              {errLine}
              {helpLine}
            </fieldset>
          );
        }

        let control: ReactNode;
        const common = {
          id,
          disabled,
          required: f.required,
          "aria-invalid": err ? true : undefined,
          "aria-describedby": describedBy,
        } as const;
        if (f.kind === "enum") {
          const titles = f.s["x-enum-titles"];
          control = (
            <select className="input" {...common} value={String(value)} onChange={(e) => onChange(f.name, e.target.value)}>
              {!f.required && f.s.default === undefined && <option value="">Default</option>}
              {f.s.enum!.map((opt, i) => (
                <option key={String(opt)} value={String(opt)}>
                  {titles?.[i] ?? String(opt)}
                </option>
              ))}
            </select>
          );
        } else if (f.kind === "number" || f.kind === "integer") {
          const input = (
            <input
              className="input mono"
              type="number"
              inputMode={f.kind === "integer" ? "numeric" : "decimal"}
              min={f.s.minimum}
              max={f.s.maximum}
              step={f.s.multipleOf ?? (f.kind === "integer" ? 1 : "any")}
              {...common}
              value={String(value)}
              onChange={(e) => onChange(f.name, e.target.value)}
            />
          );
          control = f.s["x-unit"] ? (
            <div className="affix">
              {input}
              <span className="suffix" aria-hidden="true">
                {f.s["x-unit"]}
              </span>
            </div>
          ) : (
            input
          );
        } else if (f.kind === "password") {
          const on = shown[f.name] === true;
          control = (
            <div className="affix">
              <input
                className="input"
                type={on ? "text" : "password"}
                autoComplete="off"
                spellCheck={false}
                maxLength={f.s.maxLength}
                {...common}
                value={String(value)}
                onChange={(e) => onChange(f.name, e.target.value)}
              />
              <button
                type="button"
                className="affix-btn"
                aria-pressed={on}
                aria-label={on ? "Hide password" : "Show password"}
                disabled={disabled}
                onClick={() => setShown((m) => ({ ...m, [f.name]: !on }))}
              >
                <Icon name="eye" size={20} />
              </button>
            </div>
          );
        } else {
          control = (
            <input
              className={f.kind === "page-range" ? "input mono" : "input"}
              type="text"
              maxLength={f.s.maxLength}
              {...common}
              value={String(value)}
              onChange={(e) => onChange(f.name, e.target.value)}
            />
          );
        }
        return (
          <div className="field" key={f.name}>
            <label className="label" htmlFor={id}>
              {label(f)}
            </label>
            {control}
            {errLine}
            {helpLine}
          </div>
        );
      })}
    </>
  );
}

type AuxProps = {
  slot: string;
  title: string;
  required: boolean;
  accept: string;
  file: File | undefined;
  error?: string;
  disabled?: boolean;
  onPick: (slot: string, file: File | undefined) => void;
};

/** Auxiliary file slot (certificate, image stamp…): its own Choose file button. */
export function AuxField({ slot, title, required, accept, file, error, disabled, onPick }: AuxProps) {
  const id = `aux-${slot}`;
  return (
    <div className="field">
      <span className="label" id={`${id}-label`}>
        {title}
        {!required && <span className="muted"> (optional)</span>}
      </span>
      <div className="aux">
        <label className="btn btn-secondary">
          <Icon name="file" size={20} />
          Choose file
          <input
            type="file"
            accept={accept}
            disabled={disabled}
            aria-labelledby={`${id}-label`}
            aria-describedby={[error && `${id}-err`, `${id}-help`].filter(Boolean).join(" ")}
            onChange={(e) => {
              onPick(slot, e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        <span className="fsize">{file ? file.name : "No file chosen"}</span>
        {file && (
          <button
            type="button"
            className="icon-btn"
            aria-label={`Remove ${file.name}`}
            disabled={disabled}
            onClick={() => onPick(slot, undefined)}
          >
            <Icon name="x" size={18} />
          </button>
        )}
      </div>
      {error && (
        <span className="err" id={`${id}-err`}>
          <Icon name="alert" size={16} />
          {error}
        </span>
      )}
      <span className="help" id={`${id}-help`}>
        Used for this job only.
      </span>
    </div>
  );
}
