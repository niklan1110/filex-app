// Plan task 9 acceptance: a component using dangerouslySetInnerHTML fails `npm run lint`.
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function errors(code: string): Promise<string[]> {
  const eslint = new ESLint();
  const [result] = await eslint.lintText(code, { filePath: "src/Bad.tsx" });
  return result.messages.filter((m) => m.severity === 2).map((m) => m.ruleId ?? "");
}

describe("security lint rules (review C2 fix 3)", () => {
  it("rejects dangerouslySetInnerHTML", async () => {
    const code = [
      "export function Bad({ html }: { html: string }) {",
      "  return <div dangerouslySetInnerHTML={{ __html: html }} />;",
      "}",
      "",
    ].join("\n");
    expect(await errors(code)).toContain("react/no-danger");
  });

  it("rejects innerHTML and framed outputs", async () => {
    const sink = "export function f(el: HTMLElement, s: string) {\n  el.innerHTML = s;\n}\n";
    expect(await errors(sink)).toContain("no-restricted-syntax");
    expect(await errors('export const F = () => <iframe src="/x" />;\n')).toContain("react/forbid-elements");
  });
});
