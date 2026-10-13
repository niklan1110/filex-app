"""pdf.sign (plan task 31, review H8): PAdES B-B with an uploaded .p12, validated by pyHanko.

Certificates are made at run time (``cert.create`` and ``cryptography``): no private key is
committed to the repository.
"""

import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pikepdf
import pypdfium2
import pytest
from asn1crypto import x509 as asn1_x509
from conftest import CORPUS, PDF, StepCtx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext

from filex.api.routes import _split_options
from filex.registry import StepError
from filex.tools.security import cert_create, pdf_sign
from filex.tools.security.pdf_sign import Options, run
from filex.worker import step

PASS = "Signing-Pass-2026"


@pytest.fixture(scope="module")
def p12(tmp_path_factory) -> Path:
    """A .p12 made by cert.create, as a user would upload it."""
    out = tmp_path_factory.mktemp("cert")
    (made,) = cert_create.run(
        StepCtx(out), [], cert_create.Options(common_name="Jane Doe", passphrase=PASS)
    )
    return made.path


def _cert_of(path: Path, passphrase: str = PASS) -> asn1_x509.Certificate:
    _, cert, _ = pkcs12.load_key_and_certificates(path.read_bytes(), passphrase.encode())
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def make_p12(path: Path, key, *, days=(-1, 30), passphrase=PASS) -> Path:
    """A .p12 with ``key`` and a self-signed certificate valid ``days`` around now."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=days[0]))
        .not_valid_after(now + timedelta(days=days[1]))
        .sign(key, None if isinstance(key, ed25519.Ed25519PrivateKey) else hashes.SHA256())
    )
    enc = serialization.BestAvailableEncryption(passphrase.encode())
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"t", key, cert, None, enc))
    return path


def sign(ctx, p12: Path, path=PDF / "text_1p.pdf", **options) -> Path:
    ctx.aux["certificate"] = p12
    (out,) = run(ctx, Path(path), Options(**({"passphrase": PASS} | options)))
    assert out.parent == ctx.out_dir and out.name == "signed.pdf"
    assert sorted(p.name for p in ctx.out_dir.iterdir()) == ["signed.pdf"]  # no temp files
    return out


def validate(path: Path, root: asn1_x509.Certificate) -> list:
    """pyHanko's status for every signature in ``path``, trusting ``root`` only, offline."""
    context = ValidationContext(trust_roots=[root], allow_fetching=False)
    with open(path, "rb") as f:
        reader = PdfFileReader(f)
        return [validate_pdf_signature(s, context) for s in reader.embedded_signatures]


def assert_whole_file_pades(status) -> None:
    assert status.intact and status.valid and status.trusted, status.pretty_print_details()
    assert status.coverage == SignatureCoverageLevel.ENTIRE_FILE
    assert status.modification_level == ModificationLevel.NONE
    assert status.timestamp_validity is None  # B-B: no timestamp fetched


def test_invisible_signature_validates_as_intact_pades(step_ctx, p12):
    out = sign(step_ctx, p12, reason="Approved", location="Berlin")
    (status,) = validate(out, _cert_of(p12))
    assert_whole_file_pades(status)
    with pikepdf.open(out) as pdf:
        (field,) = [f for f in pdf.Root.AcroForm.Fields if f.FT == "/Sig"]
        assert field.V.SubFilter == "/ETSI.CAdES.detached"
        assert str(field.V.Reason) == "Approved" and str(field.V.Location) == "Berlin"
        assert [float(v) for v in field.Rect] == [0, 0, 0, 0]  # invisible
        assert b"Text PDF" in pdf.pages[0].Contents.read_bytes()
    assert step_ctx.warnings == []


def test_ecdsa_certificate_signs(step_ctx, tmp_path):
    p12 = make_p12(tmp_path / "ec.p12", ec.generate_private_key(ec.SECP384R1()))
    (status,) = validate(sign(step_ctx, p12), _cert_of(p12))
    assert_whole_file_pades(status)


def test_signing_is_incremental_and_keeps_earlier_signatures(step_ctx, p12, tmp_path):
    first = sign(step_ctx, p12, reason="First")
    again = tmp_path / "again"
    again.mkdir()
    ctx = StepCtx(again, ["contract.pdf"])
    second = sign(ctx, p12, first, reason="Second")
    assert second.read_bytes().startswith(first.read_bytes())  # incremental update
    older, newer = validate(second, _cert_of(p12))
    assert older.intact and older.valid and older.trusted
    assert older.coverage == SignatureCoverageLevel.ENTIRE_REVISION
    assert older.modification_level <= ModificationLevel.FORM_FILLING
    assert_whole_file_pades(newer)
    assert [c for c, _ in ctx.warnings] == ["ALREADY_SIGNED"]
    assert '"contract.pdf" already had 1 signature' in ctx.warnings[0][1]


def _rect(path: Path, index: int) -> list[float]:
    with pikepdf.open(path) as pdf:
        (field,) = [f for f in pdf.Root.AcroForm.Fields if f.FT == "/Sig"]
        assert field.P.objgen == pdf.pages[index].objgen and "/N" in field.AP
        return [float(v) for v in field.Rect]


def _ink(path: Path, index: int, rect: list[float]) -> float:
    """Share of dark pixels inside ``rect`` on an unrotated page rendered with its widgets."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        pdf.init_forms()
        page = pdf[index]
        image = page.render(scale=1, may_draw_forms=True).to_pil().convert("L")
        x1, y1, x2, y2 = rect
        h = page.get_height()
        box = image.crop((int(x1), int(h - y2), int(x2), int(h - y1)))
        return sum(box.histogram()[:200]) / (box.width * box.height)
    finally:
        pdf.close()


def test_visible_stamp_on_chosen_page_and_position(step_ctx, p12):
    out = sign(step_ctx, p12, PDF / "text_10p.pdf", visible=True, page=3, position="top-left")
    rect = _rect(out, 2)
    assert rect[0] == pytest.approx(36, abs=1) and rect[3] == pytest.approx(841.89 - 36, abs=1)
    assert rect[2] - rect[0] == pytest.approx(200, abs=1)
    assert rect[3] - rect[1] == pytest.approx(64, abs=1)
    (status,) = validate(out, _cert_of(p12))
    assert_whole_file_pades(status)


def test_page_zero_is_the_last_page(step_ctx, p12):
    out = sign(step_ctx, p12, PDF / "text_10p.pdf", visible=True, page=0)
    rect = _rect(out, 9)
    assert rect[2] == pytest.approx(595.28 - 36, abs=1) and rect[1] == pytest.approx(36, abs=1)


def test_visible_stamp_on_a_rotated_page_sits_where_the_reader_sees_it(step_ctx, p12):
    out = sign(step_ctx, p12, PDF / "mixed_pages.pdf", visible=True, page=4, position="top-left")
    x1, y1, x2, y2 = _rect(out, 3)  # /Rotate 90: displayed top-left is user-space bottom-left
    assert x2 - x1 == pytest.approx(64, abs=1) and y2 - y1 == pytest.approx(200, abs=1)
    assert x1 == pytest.approx(36, abs=1) and y1 == pytest.approx(36, abs=1)


def test_visible_stamp_with_image_and_unicode_text_is_drawn(step_ctx, p12):
    step_ctx.aux["stamp"] = CORPUS / "image" / "graphic_rgba.png"
    out = sign(step_ctx, p12, visible=True, reason="Согласовано", location="Москва")
    rect = _rect(out, 0)
    assert _ink(out, 0, rect) > 0.02
    (status,) = validate(out, _cert_of(p12))
    assert_whole_file_pades(status)


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("stamp.jpg", CORPUS / "image" / "graphic_rgba.png"),  # decoder pinned (review L-3)
        ("stamp.png", CORPUS / "odd" / "png_huge_dimensions.png"),  # pixel cap
    ],
)
def test_bad_stamp_image_is_refused_before_decoding(step_ctx, tmp_path, name, source):
    stamp = tmp_path / name
    stamp.write_bytes(source.read_bytes())
    step_ctx.aux["stamp"] = stamp
    with pytest.raises(StepError) as e:
        pdf_sign._stamp_image(step_ctx)
    assert e.value.code == "INVALID_INPUT"


def test_page_past_the_end_is_refused(step_ctx, p12):
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12, visible=True, page=2)
    assert e.value.code == "INVALID_OPTIONS" and "1 pages" in e.value.message


def test_wrong_passphrase_is_declared_and_never_echoed(step_ctx, p12):
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12, passphrase="Wrong-Canary-0001")
    assert e.value.code == "WRONG_PASSPHRASE" and "Canary" not in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


def test_missing_passphrase_says_one_is_needed(step_ctx, p12):
    step_ctx.aux["certificate"] = p12
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options())
    assert e.value.code == "WRONG_PASSPHRASE" and "needs its passphrase" in e.value.message


@pytest.mark.parametrize(
    "key",
    [
        dsa.generate_private_key(2048),
        ed25519.Ed25519PrivateKey.generate(),
        rsa.generate_private_key(65537, 1024),
        ec.generate_private_key(ec.SECP256K1()),
    ],
    ids=["dsa", "ed25519", "rsa-1024", "secp256k1"],
)
def test_unsupported_keys_are_refused(step_ctx, tmp_path, key):
    with pytest.raises(StepError) as e:
        sign(step_ctx, make_p12(tmp_path / "k.p12", key))
    assert e.value.code == "UNSUPPORTED_KEY"


def test_expired_certificate_is_refused(step_ctx, tmp_path):
    p12 = make_p12(tmp_path / "old.p12", ec.generate_private_key(ec.SECP256R1()), days=(-30, -1))
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12)
    assert e.value.code == "CERTIFICATE_EXPIRED"


def test_a_file_that_is_not_pkcs12_is_refused(step_ctx, tmp_path):
    bad = tmp_path / "bad.p12"
    bad.write_bytes(b"0\x82\x01\x00not really asn1")
    with pytest.raises(StepError) as e:
        sign(step_ctx, bad)
    assert e.value.code == "INVALID_CERTIFICATE"


@pytest.mark.parametrize("name", ["encrypted_aes256.pdf", "encrypted_owner_only.pdf"])
def test_encrypted_inputs_are_refused(step_ctx, p12, name):
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12, PDF / name)
    assert e.value.code == "ENCRYPTED_PDF"
    assert step_ctx.warnings == []


def test_damaged_input_is_repaired_then_signed(step_ctx, p12):
    out = sign(step_ctx, p12, PDF / "broken_xref.pdf")
    (status,) = validate(out, _cert_of(p12))
    assert_whole_file_pades(status)
    assert [c for c, _ in step_ctx.warnings] == ["INPUT_REPAIRED"]


def test_not_a_pdf_fails_cleanly(step_ctx, p12):
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12, CORPUS / "odd" / "text_named.pdf")
    assert e.value.code == "INVALID_PDF"
    assert list(step_ctx.out_dir.iterdir()) == []


def test_certificate_slot_is_sensitive_and_passphrase_a_secret():
    aux = pdf_sign.run.files.aux
    assert aux["certificate"].sensitive and aux["certificate"].required
    assert not aux["stamp"].required and not aux["stamp"].sensitive
    prop = Options.model_json_schema(by_alias=True)["properties"]["passphrase"]
    assert prop["writeOnly"] is True and prop["format"] == "password"
    options, secrets = _split_options(pdf_sign.run, json.dumps({"passphrase": PASS}).encode())
    assert PASS not in json.dumps(options) and secrets == {"passphrase": PASS}


def _step_request(tmp_path: Path, p12: Path, passphrase: str) -> dict:
    """What the runner sends the step child on stdin: the secret apart from the options."""
    out = tmp_path / "step-out"
    out.mkdir()
    return {
        "module": "filex.tools.security.pdf_sign",
        "tool": "pdf.sign",
        "options": {"reason": "ok", "visible": True},
        "secrets": {"passphrase": passphrase},
        "inputs": [str(PDF / "text_1p.pdf")],
        "input_names": ["contract.pdf"],
        "out_dirs": [str(out)],
        "target": None,
        "aux": {"certificate": str(p12)},
        "max_pages": 2000,
        "max_pixels": 100_000_000,
    }


@pytest.mark.parametrize("passphrase", [PASS, "Wrong-Canary-77777"])
def test_step_child_keeps_passphrase_and_key_out_of_events_logs_and_argv(
    tmp_path, caplog, monkeypatch, p12, passphrase
):
    def no_children(*args, **kwargs):
        raise AssertionError("pdf.sign must not start a process (argv is world-readable)")

    monkeypatch.setattr(subprocess, "Popen", no_children)
    caplog.set_level(logging.DEBUG)
    events = []
    req = _step_request(tmp_path, p12, passphrase)
    try:
        done = step.run(req, lambda **e: events.append(e))
        message = ""
    except StepError as e:
        done, message = [], f"{e.code} {e.message}"
    shown = json.dumps(events) + json.dumps(done) + caplog.text + message
    assert passphrase not in shown
    if passphrase == PASS:
        assert done and done[0][2] == "RESULT"
        signed = (tmp_path / "step-out" / done[0][1]).read_bytes()
        assert PASS.encode() not in signed and b"PRIVATE KEY" not in signed
        key = pkcs12.load_key_and_certificates(p12.read_bytes(), PASS.encode())[0]
        der = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        assert der[-32:] not in signed  # no key material in the output
    else:
        assert message.startswith("WRONG_PASSPHRASE")


def test_document_certified_against_changes_is_refused(step_ctx, p12, tmp_path):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import MDPPerm

    certified = tmp_path / "certified.pdf"
    signer = signers.SimpleSigner.load_pkcs12_data(p12.read_bytes(), [], PASS.encode())
    meta = signers.PdfSignatureMetadata(
        field_name="Author", certify=True, docmdp_permissions=MDPPerm.NO_CHANGES
    )
    with open(PDF / "text_1p.pdf", "rb") as f, open(certified, "wb") as out:
        signers.sign_pdf(IncrementalPdfFileWriter(f), meta, signer, output=out)
    with pytest.raises(StepError) as e:
        sign(step_ctx, p12, certified)
    assert e.value.code == "SIGNING_LOCKED" and step_ctx.warnings == []
    assert list(step_ctx.out_dir.iterdir()) == []
