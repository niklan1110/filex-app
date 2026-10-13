import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import {
  OptionsForm,
  buildOptions,
  fieldsOf,
  initialValues,
  validPageRange,
  validate,
  type JsonSchema,
  type Values,
} from "./OptionsForm";

afterEach(cleanup);

// Shapes Pydantic emits: $defs enums, Optional as anyOf null, writeOnly secrets.
const SCHEMA: JsonSchema = {
  type: "object",
  required: ["level"],
  $defs: {
    Level: { type: "string", enum: ["low", "medium", "high"] },
    Paper: { type: "string", enum: ["a3", "a4", "a5", "letter", "legal"] },
  },
  properties: {
    bookmarks: { type: "boolean", title: "Add a bookmark for each file", default: false },
    level: { $ref: "#/$defs/Level", title: "Level", "x-enum-descriptions": ["Small", "Balanced", "Best"] },
    paper: { $ref: "#/$defs/Paper", title: "Paper", default: "a4" },
    dpi: { type: "integer", title: "Resolution", minimum: 72, maximum: 600, default: 150, "x-unit": "dpi" },
    pages: { anyOf: [{ type: "string", format: "page-range" }, { type: "null" }], title: "Pages", default: null },
    password: { type: "string", writeOnly: true, title: "Password", default: "never-shown" },
    label: { type: "string", title: "Label", maxLength: 5, pattern: "^[a-z]+$", "x-pattern-message": "Lowercase letters only." },
    remote: { $ref: "https://evil.example/schema.json" },
  },
};

function Harness({ schema }: { schema: JsonSchema }) {
  const fields = fieldsOf(schema);
  const [values, setValues] = useState<Values>(() => initialValues(fields));
  return (
    <OptionsForm
      fields={fields}
      values={values}
      errors={validate(fields, values)}
      onChange={(n, v) => setValues((p) => ({ ...p, [n]: v }))}
    />
  );
}

describe("fieldsOf / initialValues / buildOptions", () => {
  const fields = fieldsOf(SCHEMA);

  it("maps each schema shape to a control kind and skips remote refs", () => {
    expect(fields.map((f) => [f.name, f.kind])).toEqual([
      ["bookmarks", "boolean"],
      ["level", "enum"],
      ["paper", "enum"],
      ["dpi", "integer"],
      ["pages", "page-range"],
      ["password", "password"],
      ["label", "string"],
    ]);
  });

  it("uses defaults, never pre-fills a secret, and leaves empty fields to the server", () => {
    const v = initialValues(fields);
    expect(v).toMatchObject({ bookmarks: false, paper: "a4", dpi: "150", pages: "", password: "" });
    expect(buildOptions(fields, { ...v, level: "high" })).toEqual({
      bookmarks: false,
      level: "high",
      paper: "a4",
      dpi: 150,
    });
  });
});

describe("validate", () => {
  const fields = fieldsOf(SCHEMA);
  const ok = { ...initialValues(fields), level: "low" };

  it("accepts the defaults once the required field is set", () => {
    expect(validate(fields, ok)).toEqual({});
    expect(validate(fields, initialValues(fields))).toEqual({ level: "Choose one." });
  });

  it("explains range, page-range and pattern errors in words", () => {
    expect(validate(fields, { ...ok, dpi: "9000" }).dpi).toBe("Use a value from 72 to 600.");
    expect(validate(fields, { ...ok, dpi: "7.5" }).dpi).toMatch(/whole number/);
    expect(validate(fields, { ...ok, pages: "1-3, x" }).pages).toMatch(/like 1-3, 5, 8-/);
    expect(validate(fields, { ...ok, label: "ABC" }).label).toBe("Lowercase letters only.");
    expect(validate(fields, { ...ok, label: "abcdefg" }).label).toBe("Use at most 5 characters.");
  });

  it.each([
    ["1-3, 5, 8-", true],
    ["-4", true],
    ["2", true],
    ["0", false],
    ["3-0", false],
    ["1,,2", false],
    ["a", false],
  ])("page range %j valid=%s", (text, valid) => {
    expect(validPageRange(text)).toBe(valid);
  });
});

describe("<OptionsForm>", () => {
  it("renders a switch, radio cards (≤ 4), a select (> 4) and a unit suffix", () => {
    render(<Harness schema={SCHEMA} />);
    const sw = screen.getByRole("switch", { name: "Add a bookmark for each file" });
    fireEvent.click(sw);
    expect((sw as HTMLInputElement).checked).toBe(true);
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByRole("group", { name: "Level" })).toBeTruthy();
    expect((screen.getByRole("combobox", { name: "Paper" }) as HTMLSelectElement).value).toBe("a4");
    const dpi = screen.getByRole("spinbutton", { name: "Resolution" });
    expect(dpi.getAttribute("min")).toBe("72");
    expect(dpi.getAttribute("max")).toBe("600");
    expect(screen.getByText(/In dpi\./)).toBeTruthy();
  });

  it("marks an invalid field and ties the message to it", () => {
    render(<Harness schema={SCHEMA} />);
    const dpi = screen.getByRole("spinbutton", { name: "Resolution" });
    fireEvent.change(dpi, { target: { value: "5" } });
    expect(dpi.getAttribute("aria-invalid")).toBe("true");
    const ids = dpi.getAttribute("aria-describedby")!.split(" ");
    expect(document.getElementById(ids[0])?.textContent).toBe("Use a value from 72 to 600.");
  });

  it("shows a password field with a show/hide toggle and an empty value", () => {
    render(<Harness schema={SCHEMA} />);
    const pw = screen.getByLabelText("Password");
    expect(pw.getAttribute("type")).toBe("password");
    expect((pw as HTMLInputElement).value).toBe("");
    expect(pw.getAttribute("autocomplete")).toBe("off");
    const toggle = screen.getByRole("button", { name: "Show password" });
    fireEvent.click(toggle);
    expect(pw.getAttribute("type")).toBe("text");
    expect(screen.getByRole("button", { name: "Hide password" }).getAttribute("aria-pressed")).toBe("true");
  });
});
