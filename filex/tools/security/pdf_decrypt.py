"""``pdf.decrypt``: remove a PDF's password and restrictions (spec 6.7, plan task 30).

A file that needs a password to open is opened with the one given (the open or the permissions
password both work) and saved without encryption; a wrong or missing password fails with
WRONG_PASSWORD or ENCRYPTED_PDF. A file that opens without a password but carries restrictions
needs none: its restrictions are removed (RESTRICTIONS_REMOVED). A file with no protection at
all is saved unchanged with NOT_ENCRYPTED. Full pikepdf rewrite.

The password is a ``Secret`` option (review H5): write-only, delivered on the step's stdin,
passed to pikepdf in-process and never quoted in a message.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from filex.registry import Example, Files, Secret, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, open_pdf, save


class Options(ToolOptions):
    password: SecretStr | None = Secret(title="Password", optional=True)


@tool(
    id="pdf.decrypt",
    title="Remove PDF password",
    summary="Unlock a PDF you have the password for, and lift its print, copy and edit limits.",
    category="security",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[Example(inputs=["pdf/encrypted_owner_only.pdf"])],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED", "NOT_ENCRYPTED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "WRONG_PASSWORD", "TOO_MANY_PAGES"],
    keywords=["unlock", "password", "decrypt", "unprotect", "restrictions", "permissions"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    password = opts.password.get_secret_value() if opts.password else ""
    try:
        pdf = open_pdf(ctx, src, what, password=password)
    except StepError as e:
        if e.code != "ENCRYPTED_PDF":
            raise
        raise StepError("ENCRYPTED_PDF", f"{what} needs its password to be unlocked.") from None
    with pdf:
        if not pdf.is_encrypted:
            ctx.warn("NOT_ENCRYPTED", f"{what} had no password or restrictions.")
        ctx.progress(0.5, "Saving")
        return [save(pdf, ctx.out_dir / "unlocked.pdf", what)]
