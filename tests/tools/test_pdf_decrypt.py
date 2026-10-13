"""pdf.decrypt on real corpus files (plan task 30). Corpus passwords: filex-user, filex-owner."""

import json
import logging
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF

from filex.registry import StepError
from filex.tools.security.pdf_decrypt import Options, run
from filex.worker import step

USER, OWNER = "filex-user", "filex-owner"


def decrypt(ctx, path, **options) -> pikepdf.Pdf:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


@pytest.mark.parametrize("password", [USER, OWNER])
def test_aes256_opens_with_either_password_and_comes_out_unencrypted(step_ctx, password):
    with decrypt(step_ctx, PDF / "encrypted_aes256.pdf", password=password) as pdf:
        assert not pdf.is_encrypted
        assert pdf.allow.modify_assembly  # restrictions gone too
        assert len(pdf.pages) >= 1
    assert step_ctx.warnings == []


def test_legacy_rc4_file(step_ctx):
    with decrypt(step_ctx, PDF / "encrypted_rc4_40.pdf", password=USER) as pdf:
        assert not pdf.is_encrypted and pdf.allow.print_highres and pdf.allow.extract


def test_wrong_password_is_declared_and_never_echoed(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "encrypted_aes256.pdf", Options(password="Guess-Canary-9"))
    assert e.value.code == "WRONG_PASSWORD"
    assert "Guess-Canary-9" not in e.value.message
    assert e.value.__cause__ is None and e.value.__suppress_context__  # no engine text chained
    assert list(step_ctx.out_dir.iterdir()) == []


def test_missing_password_says_one_is_needed(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "encrypted_aes256.pdf", Options())
    assert e.value.code == "ENCRYPTED_PDF"
    assert "needs its password" in e.value.message


def test_owner_restrictions_are_removed_without_a_password(step_ctx):
    with decrypt(step_ctx, PDF / "encrypted_owner_only.pdf") as pdf:
        assert not pdf.is_encrypted
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_an_unprotected_file_warns_and_is_returned(step_ctx):
    with decrypt(step_ctx, PDF / "text_1p.pdf") as pdf:
        assert len(pdf.pages) == 1
    assert [c for c, _ in step_ctx.warnings] == ["NOT_ENCRYPTED"]


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options(password=USER))
    assert e.value.code == "INVALID_PDF"


def _step_request(tmp_path: Path, password: str) -> dict:
    """What the runner sends the step child on stdin: the secret apart from the options."""
    out = tmp_path / "step-out"
    out.mkdir()
    return {
        "module": "filex.tools.security.pdf_decrypt",
        "tool": "pdf.decrypt",
        "options": {},
        "secrets": {"password": password},
        "inputs": [str(PDF / "encrypted_aes256.pdf")],
        "input_names": ["locked.pdf"],
        "out_dirs": [str(out)],
        "target": None,
        "aux": {},
        "max_pages": 2000,
        "max_pixels": 100_000_000,
    }


@pytest.mark.parametrize("password", [USER, "Wrong-Canary-77"])
def test_step_child_keeps_the_password_out_of_events_and_logs(tmp_path, caplog, password):
    caplog.set_level(logging.DEBUG)
    events = []
    req = _step_request(tmp_path, password)
    try:
        done = step.run(req, lambda **e: events.append(e))
        message = ""
    except StepError as e:
        done, message = [], f"{e.code} {e.message}"
    assert password not in json.dumps(events) + json.dumps(done) + caplog.text + message
    if password == USER:
        assert done and done[0][2] == "RESULT"
    else:
        assert message.startswith("WRONG_PASSWORD")
