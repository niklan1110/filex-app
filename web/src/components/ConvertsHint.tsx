import { convertsHint } from "../catalog";

/** "DOCX, XLSX, PPTX → PDF" badge for converter tools; nothing for other tools. */
export function ConvertsHint({ tool, as: Tag = "span" }: { tool: Parameters<typeof convertsHint>[0]; as?: "span" | "p" }) {
  const hint = convertsHint(tool);
  if (!hint) return null;
  return (
    <Tag className="fmts">
      <span className="fmt">
        <span className="sr-only">Converts </span>
        {hint.from}
        <span aria-hidden="true"> → </span>
        <span className="sr-only"> to </span>
        {hint.to}
      </span>
    </Tag>
  );
}
