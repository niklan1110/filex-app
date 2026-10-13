// Lint rules from review C2 fix 3 (spec 9.14). Things lint cannot see stay on the review
// checklist for every web PR:
//   - no href or src built from job data (file names, messages, output names); only FileX API
//     paths built by src/api/client.ts may go into href/src;
//   - previews are <img> or PDF.js canvas only; never <iframe>, <object> or <embed> for outputs;
//   - no runtime request to another origin (fonts and icons are self-hosted).
import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const htmlSinks = ["innerHTML", "outerHTML"].map((name) => ({
  selector: `MemberExpression[property.name='${name}']`,
  message: `No ${name}: render text through React (review C2 fix 3).`,
}));

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      "react/no-danger": "error",
      "react/no-danger-with-children": "error",
      "react/jsx-no-script-url": "error",
      "react/forbid-elements": [
        "error",
        {
          forbid: [
            { element: "iframe", message: "Outputs are never framed (spec 9.7)." },
            { element: "object", message: "Outputs are never embedded (spec 9.7)." },
            { element: "embed", message: "Outputs are never embedded (spec 9.7)." },
          ],
        },
      ],
      "no-restricted-syntax": [
        "error",
        ...htmlSinks,
        {
          selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
          message: "No insertAdjacentHTML: render text through React (review C2 fix 3).",
        },
        {
          selector: "CallExpression[callee.object.name='document'][callee.property.name=/^write/]",
          message: "No document.write.",
        },
      ],
    },
  },
);
