"""Two fake converters in two worker groups for the convert end-to-end test (task 36): the
R2 Markdown-to-PDF shape, ``md -> html`` (core) then ``html -> pdf`` (office). Each writes the
display names it was given, so the test sees what reached ``ctx.input_names``."""

from filex.registry import Edge, Files, tool


@tool(
    id="cv.md-html",
    title="Markdown to HTML",
    summary="Fake Pandoc.",
    category="convert",
    group="core",
    converts=[Edge("md", "html")],
    files=Files(min=1, max=5, each=True),
)
def md_html(ctx, src, opts):
    out = ctx.out_dir / "page.html"
    out.write_text(f"<html>{ctx.target} {list(ctx.input_names)}</html>", encoding="utf-8")
    return [out]


@tool(
    id="cv.html-pdf",
    title="HTML to PDF",
    summary="Fake WeasyPrint.",
    category="convert",
    group="office",
    converts=[Edge("html", "pdf", lossy=True)],
    files=Files(min=1, max=5, each=True),
)
def html_pdf(ctx, src, opts):
    out = ctx.out_dir / "print.pdf"
    seen = src.read_text(encoding="utf-8")
    out.write_bytes(f"%PDF-1.7\n{ctx.target} {list(ctx.input_names)} {seen}\n".encode())
    return [out]
