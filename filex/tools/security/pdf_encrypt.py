"""``pdf.encrypt``: protect a PDF with AES-256 and set what readers may do (spec 6.7, task 30).

Always AES-256 (PDF 2.0 security handler, revision 6); weaker methods are not offered. The open
password (``userPassword``) is what a reader needs to open the file; leave it out to make a file
anyone can open but whose permissions apply. The permissions password (``ownerPassword``) lifts
the restrictions; when it is left out a random one is made and thrown away, so the restrictions
cannot be lifted with the open password. At least one password is required. Permissions are
honoured by well-behaved readers only: they are not a technical barrier once the file is open.

Passwords are ``Secret`` options (review H5): write-only, delivered on the step's stdin, passed
to pikepdf in-process (never on a command line or in the environment) and never in a message.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator

from filex.registry import Example, Files, Secret, StepContext, ToolOptions, tool
from filex.tools._pdf import label, open_pdf, save


class Options(ToolOptions):
    user_password: SecretStr | None = Secret(title="Password to open the file", optional=True)
    owner_password: SecretStr | None = Secret(
        title="Password to change permissions (blank: none can)", optional=True
    )
    allow_print: bool = Field(True, title="Allow printing")
    allow_copy: bool = Field(True, title="Allow copying text and images")
    allow_modify: bool = Field(True, title="Allow editing, comments, forms and page changes")

    @model_validator(mode="after")
    def _one_password(self) -> Self:
        if not any(p and p.get_secret_value() for p in (self.user_password, self.owner_password)):
            raise ValueError("Enter a password to open the file, to change permissions, or both.")
        return self


@tool(
    id="pdf.encrypt",
    title="Protect PDF with a password",
    summary="Lock a PDF with AES-256 and choose whether it can be printed, copied or edited.",
    category="security",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[
        Example(
            inputs=["pdf/text_1p.pdf"],
            options={"userPassword": "example-open", "allowCopy": False},
        )
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["password", "encrypt", "lock", "protect", "aes", "permissions", "secure"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf

    what = label(ctx)
    user = opts.user_password.get_secret_value() if opts.user_password else ""
    owner = opts.owner_password.get_secret_value() if opts.owner_password else ""
    edit = opts.allow_modify
    allow = pikepdf.Permissions(
        accessibility=True,
        extract=opts.allow_copy,
        print_lowres=opts.allow_print,
        print_highres=opts.allow_print,
        modify_annotation=edit,
        modify_assembly=edit,
        modify_form=edit,
        modify_other=edit,
    )
    encryption = pikepdf.Encryption(
        owner=owner or secrets.token_urlsafe(32), user=user, R=6, allow=allow, aes=True
    )
    with open_pdf(ctx, src, what) as pdf:
        ctx.progress(0.5, "Encrypting")
        return [save(pdf, ctx.out_dir / "protected.pdf", what, encryption=encryption)]
