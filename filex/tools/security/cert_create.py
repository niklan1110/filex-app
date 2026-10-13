"""``cert.create``: make a self-signed signing certificate (spec 6.7, plan task 31, review H8).

The key pair (RSA-3072 or ECDSA P-256) is generated inside the step and leaves it only as a
``.p12`` encrypted with ``cryptography``'s ``BestAvailableEncryption`` (AES-256 with PBKDF2,
never RC2 or 3DES) under a passphrase of at least 12 characters. The key is never written
anywhere else, logged or kept: FileX holds no keys, and the ``.p12`` is an ordinary result that
expires with the job. The certificate is for signing only: not a CA, key usage digital
signature and non-repudiation, so readers show the signer as "not trusted" until the recipient
trusts it.

The passphrase is a ``Secret`` option (review H5): write-only, delivered on the step's stdin and
passed to ``cryptography`` in-process.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator

from filex.registry import Files, Limits, Output, Secret, StepContext, ToolOptions, tool

MIN_PASSPHRASE = 12
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Options(ToolOptions):
    common_name: str = Field(title="Your name (shown as the signer)", min_length=1, max_length=64)
    organization: str = Field("", title="Organisation", max_length=64)
    email: str = Field("", title="Email", max_length=128)
    key_type: Literal["rsa-3072", "ecdsa-p256"] = Field(
        "rsa-3072",
        title="Key type",
        json_schema_extra={
            "x-enum-titles": ["RSA 3072", "ECDSA P-256"],
            "x-enum-descriptions": ["Works with every reader", "Smaller; some old readers fail"],
        },
    )
    validity_days: int = Field(365, title="Valid for (days)", ge=1, le=3650)
    passphrase: SecretStr = Secret(
        title=f"Passphrase for the .p12 (at least {MIN_PASSPHRASE} characters)",
        min_length=MIN_PASSPHRASE,
    )

    @field_validator("common_name", "organization")
    @classmethod
    def _plain_text(cls, v: str, info: ValidationInfo) -> str:
        v = v.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("Use plain text without control characters.")
        if info.field_name == "common_name" and not v:
            raise ValueError("Enter a name.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if v and not (_EMAIL.fullmatch(v) and v.isascii()):
            raise ValueError("Enter an email address like name@example.com, or leave it blank.")
        return v


@tool(
    id="cert.create",
    title="Create signing certificate",
    summary=(
        "Make a self-signed certificate for signing PDFs, as a .p12 file locked with your "
        "passphrase. FileX keeps no copy of the key."
    ),
    category="security",
    group="core",
    outputs=["p12"],
    files=Files(min=0, max=0),
    options=Options,
    engines=["cryptography"],
    limits=Limits(timeout_s=60, memory_mb=256),
    keywords=["certificate", "p12", "pfx", "pkcs12", "self-signed", "key", "signature"],
)
def run(ctx: StepContext, src: list[Path], opts: Options) -> list[Output]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    ctx.progress(0.1, "Generating the key")
    if opts.key_type == "rsa-3072":
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    else:
        key = ec.generate_private_key(ec.SECP256R1())

    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, opts.common_name)]
    if opts.organization:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, opts.organization))
    if opts.email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, opts.email))
    name = x509.Name(attrs)
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))  # clock skew between machines
        .not_valid_after(now + timedelta(days=opts.validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if opts.email:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(opts.email)]), critical=False
        )
    cert = builder.sign(key, hashes.SHA256())

    ctx.progress(0.7, "Encrypting the .p12")
    data = pkcs12.serialize_key_and_certificates(
        opts.common_name.encode(),
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(opts.passphrase.get_secret_value().encode()),
    )
    out = ctx.out_dir / "certificate.p12"
    out.write_bytes(data)
    return [Output(out, f"{opts.common_name}.p12")]
