"""cert.create (plan task 31, review H8): a self-signed signing certificate as an AES .p12."""

import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from asn1crypto import pkcs12 as asn1_pkcs12
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from filex.api.deps import ApiError
from filex.api.routes import _split_options
from filex.formats import sniff
from filex.tools.security import cert_create
from filex.tools.security.cert_create import Options, run

PASS = "Correct-Horse-4711"


def create(ctx, **options):
    (out,) = run(ctx, [], Options(**({"passphrase": PASS} | options)))
    assert out.path.parent == ctx.out_dir
    return out


def load(path, passphrase=PASS):
    key, cert, extra = pkcs12.load_key_and_certificates(path.read_bytes(), passphrase.encode())
    assert extra == []
    return key, cert


def test_rsa_3072_certificate_with_subject_fields(step_ctx):
    out = create(step_ctx, common_name="Jane Doe", organization="Doe & Co", email="jane@doe.org")
    assert out.name == "Jane Doe.p12" and sniff(out.path) == "p12"
    key, cert = load(out.path)
    assert isinstance(key, rsa.RSAPrivateKey) and key.key_size == 3072
    subject = {a.oid: a.value for a in cert.subject}
    assert subject == {
        NameOID.COMMON_NAME: "Jane Doe",
        NameOID.ORGANIZATION_NAME: "Doe & Co",
        NameOID.EMAIL_ADDRESS: "jane@doe.org",
    }
    assert cert.issuer == cert.subject  # self-signed
    assert cert.public_key() == key.public_key()
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.RFC822Name) == ["jane@doe.org"]
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.digital_signature and usage.value.content_commitment
    assert not usage.value.key_cert_sign
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


def test_ecdsa_p256_and_validity(step_ctx):
    out = create(step_ctx, common_name="Иван Петров", key_type="ecdsa-p256", validity_days=30)
    key, cert = load(out.path)
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1"
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Иван Петров"
    now = datetime.now(UTC)
    assert cert.not_valid_before_utc <= now
    assert abs(cert.not_valid_after_utc - (now + timedelta(days=30))) < timedelta(minutes=1)


def test_each_run_makes_a_new_key(step_ctx, tmp_path):
    first = load(create(step_ctx, common_name="A").path)[0]
    other = tmp_path / "other"
    other.mkdir()
    step_ctx.out_dir = other
    second = load(create(step_ctx, common_name="A").path)[0]
    assert first.private_numbers() != second.private_numbers()


def _algorithms(data: bytes) -> list[str]:
    """Every encryption algorithm used inside a .p12 (key bags and encrypted cert bags)."""
    pfx = asn1_pkcs12.Pfx.load(data)
    found = []
    for info in pfx.authenticated_safe:
        if info["content_type"].native == "encrypted_data":
            alg = info["content"]["encrypted_content_info"]["content_encryption_algorithm"]
            found.append(_name(alg))
            continue
        for bag in asn1_pkcs12.SafeContents.load(info["content"].native):
            if bag["bag_id"].native == "pkcs8_shrouded_key_bag":
                found.append(_name(bag["bag_value"]["encryption_algorithm"]))
    return found


def _name(alg) -> str:
    if alg["algorithm"].native == "pbes2":
        return "pbes2/" + alg["parameters"]["encryption_scheme"]["algorithm"].native
    return alg["algorithm"].native


def test_p12_uses_aes_not_rc2_or_3des(step_ctx):
    data = create(step_ctx, common_name="Jane").path.read_bytes()
    algorithms = _algorithms(data)
    assert algorithms and all(a == "pbes2/aes256_cbc" for a in algorithms), algorithms


def test_wrong_passphrase_does_not_open_the_p12(step_ctx):
    out = create(step_ctx, common_name="Jane")
    with pytest.raises(ValueError):
        load(out.path, "Wrong-Passphrase-1")


def test_an_11_character_passphrase_is_refused_with_422():
    raw = json.dumps({"commonName": "Jane", "passphrase": "Short-pass1"}).encode()
    with pytest.raises(ApiError) as e:
        _split_options(cert_create.run, raw)
    assert e.value.status == 422 and e.value.code == "INVALID_OPTIONS"
    assert "Short-pass1" not in json.dumps(e.value.details) + e.value.message
    Options(common_name="Jane", passphrase="x" * 12)  # 12 is enough


@pytest.mark.parametrize(
    "options",
    [
        {"common_name": ""},
        {"common_name": "   "},
        {"common_name": "a\nb"},
        {"common_name": "a" * 65},
        {"common_name": "A", "email": "not-an-email"},
        {"common_name": "A", "validity_days": 0},
        {"common_name": "A", "validity_days": 3651},
        {"common_name": "A", "key_type": "dsa-1024"},
    ],
)
def test_bad_options_are_refused(options):
    with pytest.raises(ValidationError):
        Options(**({"passphrase": PASS} | options))


def test_passphrase_is_a_write_only_secret_and_split_off(step_ctx):
    prop = Options.model_json_schema(by_alias=True)["properties"]["passphrase"]
    assert prop["writeOnly"] is True and prop["format"] == "password" and prop["minLength"] == 12
    raw = json.dumps({"commonName": "Jane", "passphrase": PASS}).encode()
    options, secrets = _split_options(cert_create.run, raw)
    assert PASS not in json.dumps(options) and secrets == {"passphrase": PASS}


def test_passphrase_and_key_never_reach_logs_messages_or_a_command_line(
    step_ctx, caplog, monkeypatch
):
    def no_children(*args, **kwargs):
        raise AssertionError("cert.create must not start a process (argv is world-readable)")

    monkeypatch.setattr(subprocess, "Popen", no_children)
    caplog.set_level(logging.DEBUG)
    opts = Options(common_name="Jane", passphrase=PASS)
    (out,) = run(step_ctx, [], opts)
    shown = " ".join([caplog.text, repr(opts), str(opts), opts.model_dump_json(), repr(out)])
    assert PASS not in shown
    data = out.path.read_bytes()
    assert PASS.encode() not in data and b"PRIVATE KEY" not in data
    assert [p.name for p in step_ctx.out_dir.iterdir()] == ["certificate.p12"]  # nothing else
