"""pdf.encrypt on real corpus files (plan task 30): AES-256, permissions, secret passwords."""

import json
import logging
import subprocess
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.api.deps import ApiError
from filex.api.routes import _split_options
from filex.registry import StepError
from filex.tools.security import pdf_encrypt
from filex.tools.security.pdf_encrypt import Options, run

USER, OWNER = "open-Sesame-4711", "owner-Kraken-0815"


def encrypt(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def test_aes256_with_open_password_and_restrictions(step_ctx):
    out = encrypt(
        step_ctx,
        PDF / "text_1p.pdf",
        user_password=USER,
        owner_password=OWNER,
        allow_print=False,
        allow_copy=False,
    )
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out)
    with pikepdf.open(out, password=USER) as pdf:
        assert pdf.encryption.bits == 256 and pdf.encryption.R == 6
        assert pdf.encryption.stream_method.name == "aesv3"
        assert pdf.user_password_matched and not pdf.owner_password_matched
        allow = pdf.allow
        assert not allow.print_highres and not allow.print_lowres and not allow.extract
        assert allow.modify_other and allow.modify_annotation and allow.accessibility
        assert b"Text PDF" in pdf.pages[0].Contents.read_bytes()
    with pikepdf.open(out, password=OWNER) as pdf:
        assert pdf.owner_password_matched
    assert step_ctx.warnings == []


def test_owner_password_only_opens_freely_but_keeps_restrictions(step_ctx):
    out = encrypt(step_ctx, PDF / "text_1p.pdf", owner_password=OWNER, allow_modify=False)
    with pikepdf.open(out) as pdf:
        assert pdf.is_encrypted and not pdf.owner_password_matched
        assert not pdf.allow.modify_other and not pdf.allow.modify_form
        assert not pdf.allow.modify_assembly and pdf.allow.print_highres


def test_without_owner_password_the_open_password_cannot_lift_restrictions(step_ctx):
    out = encrypt(step_ctx, PDF / "text_1p.pdf", user_password=USER, allow_copy=False)
    with pikepdf.open(out, password=USER) as pdf:
        assert not pdf.owner_password_matched and not pdf.allow.extract


def test_unicode_password(step_ctx):
    out = encrypt(step_ctx, PDF / "text_1p.pdf", user_password="пароль-Ключ")
    with pikepdf.open(out, password="пароль-Ключ") as pdf:
        assert len(pdf.pages) == 1


def test_a_password_is_required():
    with pytest.raises(ValidationError):
        Options()
    with pytest.raises(ValidationError):
        Options(user_password="", owner_password="")


def test_password_fields_are_write_only_secrets():
    props = Options.model_json_schema(by_alias=True)["properties"]
    for name in ("userPassword", "ownerPassword"):
        assert props[name]["writeOnly"] is True and props[name]["format"] == "password"
        assert "default" not in props[name] or props[name]["default"] is None


def test_passwords_never_reach_logs_messages_job_json_or_a_command_line(
    step_ctx, caplog, monkeypatch
):
    def no_children(*args, **kwargs):
        raise AssertionError("pdf.encrypt must not start a process (argv is world-readable)")

    monkeypatch.setattr(subprocess, "Popen", no_children)
    caplog.set_level(logging.DEBUG)
    opts = Options(user_password=USER, owner_password=OWNER)
    (out,) = run(step_ctx, PDF / "text_1p.pdf", opts)
    shown = " ".join(
        [
            caplog.text,
            repr(opts),
            str(opts),
            opts.model_dump_json(),
            *(m for _, m in step_ctx.warnings),
        ]
    )
    assert USER not in shown and OWNER not in shown
    assert USER.encode() not in out.read_bytes() and OWNER.encode() not in out.read_bytes()

    raw = json.dumps({"userPassword": USER, "ownerPassword": OWNER, "allowCopy": False})
    options, secrets = _split_options(pdf_encrypt.run, raw.encode())
    assert USER not in json.dumps(options) and OWNER not in json.dumps(options)
    assert secrets == {"userPassword": USER, "ownerPassword": OWNER}


def test_a_rejected_password_is_not_echoed_in_the_422(step_ctx):
    long = "x" * 990 + "LEAK-CANARY-1234"  # over max_length 1000
    with pytest.raises(ApiError) as e:
        _split_options(pdf_encrypt.run, json.dumps({"userPassword": long}).encode())
    assert "LEAK-CANARY" not in json.dumps(e.value.details) + e.value.message


def test_a_password_protected_input_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "encrypted_aes256.pdf", Options(user_password=USER))
    assert e.value.code == "ENCRYPTED_PDF"


def test_owner_restricted_input_is_re_encrypted_and_warns(step_ctx):
    out = encrypt(step_ctx, PDF / "encrypted_owner_only.pdf", user_password=USER)
    with pikepdf.open(out, password=USER) as pdf:
        assert pdf.encryption.bits == 256
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options(user_password=USER))
    assert e.value.code == "INVALID_PDF"
