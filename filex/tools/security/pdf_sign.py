"""``pdf.sign``: sign a PDF with the user's own certificate (spec 6.7, plan task 31, review H8).

PAdES B-B (``/ETSI.CAdES.detached``, SHA-256) with pyHanko, in-process. No timestamp, OCSP or
CRL is fetched: workers have no network (review H6), so the signing time is the one the signer
claims. The key comes only from the ``.p12`` uploaded in the sensitive ``certificate`` slot,
which the runner deletes as soon as the step ends, and the passphrase only from the step's stdin
(a ``Secret`` option, review H5). Neither is logged, quoted or written anywhere. FileX holds no
keys.

The signature is an incremental update, so earlier signatures stay valid (ALREADY_SIGNED says
the file had some). A document whose certification signature forbids changes is refused
(SIGNING_LOCKED); so is one with a password or editing restrictions (ENCRYPTED_PDF: remove them
first). A damaged file is rewritten by pikepdf before signing (INPUT_REPAIRED).

Keys: RSA of at least 2048 bits or ECDSA on P-256, P-384 or P-521; anything else is
UNSUPPORTED_KEY. A certificate outside its validity period is CERTIFICATE_EXPIRED.

The visible stamp (optional) is drawn with reportlab (Unicode font, see ``_overlay``) as a small
PDF that pyHanko uses as the signature's appearance, placed at one of nine positions on the
chosen page as the reader sees it, whatever the page's ``/Rotate``.
"""

from __future__ import annotations

import io
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr

from filex.registry import Aux, Files, Limits, Secret, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label
from filex.tools.edit._overlay import POSITION_TITLES, Position, centre, font_for

STAMP_W, STAMP_H, MARGIN = 200.0, 64.0, 36.0  # points, as the reader sees the page
_CURVES = {"secp256r1", "secp384r1", "secp521r1"}
_LOCKED = "SIGNING_LOCKED"


class Options(ToolOptions):
    passphrase: SecretStr | None = Secret(title="Certificate passphrase", optional=True)
    reason: str = Field("", title="Reason", max_length=200)
    location: str = Field("", title="Location", max_length=200)
    visible: bool = Field(False, title="Show a signature stamp on the page")
    page: int = Field(1, title="Stamp page (0 = last page)", ge=0, le=100_000)
    position: Position = Field(
        "bottom-right",
        title="Stamp position",
        json_schema_extra={"x-enum-titles": POSITION_TITLES},
    )


@tool(
    id="pdf.sign",
    title="Sign PDF",
    summary=(
        "Sign a PDF with your own .p12 certificate (PAdES, no timestamp), invisibly or with a "
        "visible stamp. FileX keeps no keys: the .p12 is deleted when the step ends, but while "
        "it runs another compromised step on the server could read it."
    ),
    category="security",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    options=Options,
    files=Files(
        min=1,
        max=20,
        each=True,
        aux={
            "certificate": Aux(
                formats=["p12"], required=True, sensitive=True, title="Certificate (.p12)"
            ),
            "stamp": Aux(formats=["png", "jpg"], required=False, title="Stamp image"),
        },
    ),
    engines=["pyhanko", "pikepdf", "reportlab", "pillow"],
    limits=Limits(timeout_s=60, memory_mb=512),
    warnings=["INPUT_REPAIRED", "ALREADY_SIGNED"],
    errors=[
        "INVALID_PDF",
        "ENCRYPTED_PDF",
        "TOO_MANY_PAGES",
        "WRONG_PASSPHRASE",
        "INVALID_CERTIFICATE",
        "UNSUPPORTED_KEY",
        "CERTIFICATE_EXPIRED",
        _LOCKED,
        "UNSUPPORTED_TEXT",
    ],
    keywords=["signature", "esign", "digital", "pades", "certificate", "p12", "pfx"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    p12 = ctx.aux["certificate"].read_bytes()
    passphrase = opts.passphrase.get_secret_value() if opts.passphrase else ""
    key, cert = _load_p12(p12, passphrase)
    signer_name = _check_key(key, cert)

    ctx.progress(0.2, "Checking the PDF")
    target = ctx.out_dir / "signed.pdf"
    repaired, stamp = ctx.out_dir / ".repaired.pdf", ctx.out_dir / ".stamp.pdf"
    try:
        geometry = _prepare(ctx, src, repaired, what, opts)
        if opts.visible:
            _draw_stamp(ctx, stamp, geometry, signer_name, opts)
        ctx.progress(0.5, "Signing")
        source = repaired if repaired.exists() else src
        _sign(ctx, source, target, what, p12, opts, geometry, stamp if opts.visible else None)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:  # scratch files only; the runner deletes the uploaded .p12
        repaired.unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)
    return [target]


def _load_p12(data: bytes, passphrase: str) -> tuple[Any, Any]:
    """Key and certificate. A file that is not PKCS#12 is
    INVALID_CERTIFICATE; one that does not decrypt is WRONG_PASSPHRASE (never quoting it)."""
    from asn1crypto import pkcs12 as asn1_pkcs12
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        asn1_pkcs12.Pfx.load(data, strict=True).native  # noqa: B018 - parses the whole file
    except Exception:  # noqa: BLE001 - asn1crypto raises many types for malformed input
        raise StepError(
            "INVALID_CERTIFICATE", "The certificate file is not a valid .p12."
        ) from None
    secret = passphrase.encode()
    for attempt in [secret] if secret else [None, b""]:
        try:
            key, cert, _ = pkcs12.load_key_and_certificates(data, attempt)
            break
        except ValueError:
            continue
    else:
        message = (
            "The passphrase does not open the certificate."
            if secret
            else "The certificate needs its passphrase."
        )
        raise StepError("WRONG_PASSPHRASE", message) from None
    if key is None or cert is None:
        raise StepError(
            "INVALID_CERTIFICATE", "The .p12 must hold a private key and its certificate."
        )
    return key, cert


def _check_key(key: Any, cert: Any) -> str:
    """The signer's name for the stamp, after checking the key type and validity period."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import NameOID

    if isinstance(key, rsa.RSAPrivateKey):
        if key.key_size < 2048:
            raise StepError(
                "UNSUPPORTED_KEY", f"The RSA key has {key.key_size} bits; at least 2048 are needed."
            )
    elif not (isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name in _CURVES):
        raise StepError(
            "UNSUPPORTED_KEY", "Only RSA (2048 bits or more) and ECDSA P-256/384/521 keys can sign."
        )
    if cert.public_key() != key.public_key():
        raise StepError(
            "INVALID_CERTIFICATE", "The private key in the .p12 does not match its certificate."
        )
    now = datetime.now(UTC)
    if now > cert.not_valid_after_utc:
        raise StepError(
            "CERTIFICATE_EXPIRED",
            f"The certificate expired on {cert.not_valid_after_utc:%Y-%m-%d}.",
        )
    if now < cert.not_valid_before_utc:
        raise StepError(
            "CERTIFICATE_EXPIRED",
            f"The certificate is not valid until {cert.not_valid_before_utc:%Y-%m-%d}.",
        )
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(names[0].value) if names else cert.subject.rfc4514_string()


def _prepare(ctx: StepContext, src: Path, repaired: Path, what: str, opts: Options) -> dict:
    """Refuse what cannot be signed; rewrite a damaged file to ``repaired``. Returns the stamp
    page's index, crop box and rotation."""
    import pikepdf

    try:
        pdf = pikepdf.open(src)
    except pikepdf.PasswordError:
        raise StepError(
            "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
        ) from None
    except pikepdf.PdfError as e:
        raise StepError("INVALID_PDF", f"{what} is not a readable PDF.") from e
    with pdf:
        if pdf.is_encrypted:
            raise StepError(
                "ENCRYPTED_PDF",
                f"{what} has editing restrictions. Remove them first, then sign.",
            )
        try:
            count = len(pdf.pages)
        except pikepdf.PdfError as e:
            raise StepError("INVALID_PDF", f"{what} is damaged and cannot be read.") from e
        if not count:
            raise StepError("INVALID_PDF", f"{what} has no pages.")
        if count > ctx.max_pages:
            raise StepError(
                "TOO_MANY_PAGES", f"{what} has {count} pages; the limit is {ctx.max_pages}."
            )
        if opts.visible and opts.page > count:
            raise StepError(
                "INVALID_OPTIONS", f"{what} has {count} pages, so there is no page {opts.page}."
            )
        index = (opts.page or count) - 1 if opts.visible else 0
        page = pdf.pages[index]
        geometry = {
            "index": index,
            "box": tuple(float(v) for v in page.cropbox),
            "rotation": page.rotation % 360,
        }
        if pdf.get_warnings():
            ctx.warn("INPUT_REPAIRED", f"{what} was damaged and has been repaired.")
            try:
                pdf.save(repaired)
            except pikepdf.PdfError as e:
                raise StepError("INVALID_PDF", f"{what} is damaged and could not be saved.") from e
    return geometry


def _stamp_rect(geometry: dict) -> tuple[tuple[int, int, int, int], tuple[float, ...]]:
    """The stamp's rectangle in page user space and the matrix that maps the stamp's own
    upright drawing (STAMP_W x STAMP_H) into that rectangle's box."""
    from filex.tools.edit._overlay import _matrix

    x1, y1, x2, y2 = geometry["box"]
    x0, y0, w, h = min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
    rotation = geometry["rotation"]
    width, height = (h, w) if rotation in (90, 270) else (w, h)
    sw, sh = min(STAMP_W, width), min(STAMP_H, height)
    margin = min(MARGIN, (width - sw) / 2, (height - sh) / 2)
    cx, cy = centre(sw, sh, geometry["position"], width, height, margin)
    a, b, c, d, e, f = _matrix(rotation, x0, y0, w, h)
    corners = [(cx + dx, cy + dy) for dx in (-sw / 2, sw / 2) for dy in (-sh / 2, sh / 2)]
    xs = [a * x + c * y + e for x, y in corners]
    ys = [b * x + d * y + f for x, y in corners]
    rect = (round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys)))
    bw, bh = rect[2] - rect[0], rect[3] - rect[1]
    shown_w, shown_h = (bw, bh) if rotation in (0, 180) else (bh, bw)
    sx, sy = shown_w / STAMP_W, shown_h / STAMP_H
    m = _matrix(rotation, 0, 0, bw, bh)
    return rect, (m[0] * sx, m[1] * sx, m[2] * sy, m[3] * sy, m[4], m[5])


def _draw_stamp(ctx: StepContext, target: Path, geometry: dict, signer: str, opts: Options) -> None:
    """A one-page PDF the size of the stamp's rectangle, drawn upright for the reader."""
    from reportlab.pdfgen.canvas import Canvas

    geometry["position"] = opts.position
    rect, matrix = _stamp_rect(geometry)
    geometry["rect"] = rect
    lines = [
        f"Digitally signed by {signer}",
        f"Date: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        *([f"Reason: {opts.reason}"] if opts.reason else []),
        *([f"Location: {opts.location}"] if opts.location else []),
    ]
    font = font_for(lines[0], "signer name")
    for line, part in zip(lines[2:], ("reason", "location"), strict=False):
        font_for(line, part)
    image = _stamp_image(ctx)

    canvas = Canvas(str(target), pagesize=(rect[2] - rect[0], rect[3] - rect[1]))
    canvas.transform(*matrix)
    canvas.setFillColorRGB(1, 1, 1)  # opaque, so page content does not show through
    canvas.setStrokeColorRGB(0.1, 0.3, 0.6)
    canvas.setLineWidth(1)
    canvas.rect(0.5, 0.5, STAMP_W - 1, STAMP_H - 1, fill=1)
    left = 4.0
    if image is not None:
        side = STAMP_H - 8
        canvas.drawImage(image, 4, 4, side, side, preserveAspectRatio=True, anchor="c", mask="auto")
        left = side + 8
    _draw_lines(canvas, lines, font, left, STAMP_W - left - 4)
    canvas.showPage()
    canvas.save()


def _draw_lines(canvas: Any, lines: list[str], font: str, left: float, room: float) -> None:
    from reportlab.pdfbase import pdfmetrics

    size = 8.0
    widest = max(pdfmetrics.stringWidth(t, font, size) for t in lines)
    size = max(4.0, min(size, size * room / widest)) if widest else size
    lead = size * 1.25
    top = (STAMP_H + lead * len(lines)) / 2 - size
    canvas.setFillColorRGB(0, 0, 0)
    for i, text in enumerate(lines):
        canvas.setFont(font, size)
        while pdfmetrics.stringWidth(text, font, size) > room and len(text) > 2:
            text = text[:-2] + "…"
        canvas.drawString(left, top - i * lead, text)


def _stamp_image(ctx: StepContext) -> Any:
    """The optional stamp image as a reportlab ImageReader, refused if too large or unreadable."""
    path = ctx.aux.get("stamp")
    if path is None:
        return None
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    from filex.tools.convert.image_to_pdf import open_image

    Image.MAX_IMAGE_PIXELS = ctx.max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with open_image(path) as img:
                if img.width * img.height > ctx.max_pixels:
                    raise StepError("INVALID_INPUT", "The stamp image has too many pixels.")
                img.load()
                data = io.BytesIO()
                img.convert("RGBA").save(data, "PNG")
    except StepError:
        raise
    except (
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as e:
        raise StepError("INVALID_INPUT", "The stamp image cannot be read.") from e
    data.seek(0)
    return ImageReader(data)


def _sign(
    ctx: StepContext,
    source: Path,
    target: Path,
    what: str,
    p12: bytes,
    opts: Options,
    geometry: dict,
    stamp: Path | None,
) -> None:
    from pyhanko.pdf_utils import layout
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.misc import PdfError
    from pyhanko.sign import fields, signers
    from pyhanko.sign.fields import MDPPerm, SigSeedSubFilter
    from pyhanko.sign.validation import read_certification_data
    from pyhanko.stamp import StaticStampStyle

    signer = _signer(p12, opts)
    try:
        with open(source, "rb") as f:
            writer = IncrementalPdfFileWriter(f, strict=False)
            reader = writer.prev
            mdp = read_certification_data(reader)
            if mdp is not None and mdp.permission == MDPPerm.NO_CHANGES:
                raise StepError(_LOCKED, f"{what} is certified against any change, signing too.")
            if reader.embedded_signatures:
                n = len(reader.embedded_signatures)
                ctx.warn(
                    "ALREADY_SIGNED",
                    f"{what} already had {n} signature{'s' if n > 1 else ''}; "
                    "they were kept and still cover their part of the file.",
                )
            taken = {name for name, _, _ in fields.enumerate_sig_fields(reader)}
            name = next(
                f"Signature{i}" for i in range(1, len(taken) + 2) if f"Signature{i}" not in taken
            )
            spec = fields.SigFieldSpec(
                name,
                on_page=geometry["index"],
                box=geometry.get("rect") if stamp else None,
            )
            style = None
            if stamp:
                fill = layout.SimpleBoxLayoutRule(
                    x_align=layout.AxisAlignment.ALIGN_MID,
                    y_align=layout.AxisAlignment.ALIGN_MID,
                    margins=layout.Margins.uniform(0),
                )
                style = StaticStampStyle.from_pdf_file(
                    str(stamp), border_width=0, background_layout=fill
                )
            meta = signers.PdfSignatureMetadata(
                field_name=name,
                reason=opts.reason or None,
                location=opts.location or None,
                subfilter=SigSeedSubFilter.PADES,
                md_algorithm="sha256",
            )
            pdf_signer = signers.PdfSigner(
                meta, signer=signer, stamp_style=style, new_field_spec=spec
            )
            with open(target, "wb") as out:
                pdf_signer.sign_pdf(writer, output=out)
    except PdfError as e:
        raise StepError("INVALID_PDF", f"{what} could not be signed: it is damaged.") from e


def _signer(p12: bytes, opts: Options) -> Any:
    """pyHanko's signer from the already checked .p12."""
    from pyhanko.sign import signers

    secret = opts.passphrase.get_secret_value().encode() if opts.passphrase else None
    for attempt in [secret] if secret else [None, b""]:
        try:
            return signers.SimpleSigner.load_pkcs12_data(p12, [], passphrase=attempt)
        except ValueError:
            continue
    raise StepError("WRONG_PASSPHRASE", "The passphrase does not open the certificate.")
