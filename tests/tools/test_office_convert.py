"""office.convert (plan task 35). Pure parts run everywhere; real conversions need LibreOffice
and run in the worker-office image (``needs_lo``)."""

import zipfile
from pathlib import Path

import pytest
from conftest import CORPUS
from office_samples import linked_ods, needs_lo, plain_ods

from filex.formats import sniff
from filex.registry import StepError, ToolOptions
from filex.tools.office import _lo
from filex.tools.office.office_convert import EDGES, run

OFFICE = CORPUS / "office"


# --- pure logic


def test_edges_stay_within_a_family_and_mark_text_and_csv_lossy():
    pairs = {(e.src, e.dst) for e in EDGES}
    assert len(pairs) == len(EDGES) == 5 * 4 + 4 * 3 + 3 * 2
    assert ("docx", "odt") in pairs and ("xlsx", "csv") in pairs and ("pptx", "odp") in pairs
    assert ("docx", "xlsx") not in pairs and ("pptx", "docx") not in pairs
    assert {(e.src, e.dst) for e in EDGES if e.lossy} == {
        (a, b) for a, b in pairs if b in ("txt", "csv")
    }


@pytest.mark.parametrize(
    ("fmt", "charset", "expected"),
    [
        ("docx", "UTF8", "MS Word 2007 XML"),
        ("ods", "UTF8", "calc8"),
        ("txt", "MS_1251", "Text (encoded):MS_1251"),
        ("csv", "UTF8", "Text - txt - csv (StarCalc):44,34,76,1"),
        ("csv", "MS_1251", "Text - txt - csv (StarCalc):44,34,34,1"),
    ],
)
def test_filters(fmt, charset, expected):
    assert _lo.filter_for(fmt, charset) == expected


@pytest.mark.parametrize(
    ("data", "charset"),
    [
        (b"Hello, world\n", "UTF8"),
        (b"\xef\xbb\xbf" + "Привет, мир\n".encode(), "UTF8"),
        ("Привет, мир\n".encode("utf-16"), "UNICODE"),
        ("Привет, мир, как дела\n".encode("cp1251"), "MS_1251"),
        ("Grüße aus Köln, café\n".encode("cp1252"), "MS_1252"),
    ],
)
def test_text_charset_detection(tmp_path, data, charset):
    src = tmp_path / "0.txt"
    src.write_bytes(data)
    assert _lo.text_charset(src) == charset


def test_utf32_text_is_refused(tmp_path):
    src = tmp_path / "0.txt"
    src.write_bytes("hi".encode("utf-32"))
    with pytest.raises(StepError) as e:
        _lo.text_charset(src)
    assert e.value.code == "INVALID_INPUT"


def test_csv_import_turns_formula_evaluation_off(tmp_path):
    # Tools review L-5: token 13 "Import as formulas" (default true) set to false; tokens 5-12
    # are filled with their import defaults to reach it (LibreOffice help, csv_params).
    src = tmp_path / "0.csv"
    src.write_bytes(b"a,=1+1\n")
    args = _lo.command(src, "csv", "ods", tmp_path / "p", tmp_path)
    tokens = next(a for a in args if a.startswith("--infilter=")).split(":", 1)[1].split(",")
    assert tokens[:4] == ["44", "34", "76", "1"]
    assert tokens[12] == "false" and len(tokens) == 13


def test_a_missing_target_is_an_options_error(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "budget.xlsx", ToolOptions())
    assert e.value.code == "INVALID_OPTIONS"


def test_a_target_outside_the_family_is_refused(step_ctx):
    step_ctx.target = "docx"
    step_ctx.input_names = ["budget.xlsx"]
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "budget.xlsx", ToolOptions())
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == '"budget.xlsx" (Excel workbook) cannot be converted to Word document.'


def test_the_target_picks_extension_and_export_filter(step_ctx, tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.mkdir()
    monkeypatch.setattr(_lo, "TEMPLATE", template)
    seen = []

    def fake(args, work, timeout_s, what):
        seen.append(args)
        out = Path(args[args.index("--outdir") + 1])
        (out / (Path(args[-1]).stem + ".csv")).write_text("a,b\n")
        return ""

    monkeypatch.setattr(_lo, "run_soffice", fake)
    step_ctx.target = "csv"
    (out,) = run(step_ctx, OFFICE / "budget.xlsx", ToolOptions())
    assert out == step_ctx.out_dir / "converted.csv"
    args = seen[0]
    assert "--infilter=Calc MS Excel 2007 XML" in args
    assert args[args.index("--convert-to") + 1] == "csv:Text - txt - csv (StarCalc):44,34,76,1"


# --- real LibreOffice (worker-office image)


def _convert(ctx, src: Path, target: str, out_dir: Path) -> Path:
    out_dir.mkdir(exist_ok=True)
    ctx.out_dir, ctx.target = out_dir, target
    (out,) = run(ctx, src, ToolOptions())
    assert sniff(out, out.name) == target
    return out


@needs_lo
@pytest.mark.parametrize(
    ("name", "target"),
    [("report.docx", "odt"), ("budget.xlsx", "ods"), ("slides.pptx", "odp")],
)
def test_round_trip_samples_open_again(step_ctx, tmp_path, name, target):
    there = _convert(step_ctx, OFFICE / name, target, tmp_path / "there")
    back = _convert(step_ctx, there, Path(name).suffix[1:], tmp_path / "back")
    assert back.stat().st_size > 0


@needs_lo
def test_xlsx_to_csv_keeps_the_first_sheet_values(step_ctx, tmp_path):
    csv = _convert(step_ctx, OFFICE / "budget.xlsx", "csv", tmp_path / "c")
    lines = csv.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '"Month","Income","Costs","Profit"'
    assert lines[-1].startswith('"Total",6999')


@needs_lo
def test_csv_formulas_come_through_as_text(step_ctx, tmp_path):
    src = tmp_path / "0.csv"
    src.write_bytes(b"a,=1+1\n")
    out = _convert(step_ctx, src, "ods", tmp_path / "o")
    with zipfile.ZipFile(out) as zf:
        content = zf.read("content.xml").decode("utf-8")
    assert "table:formula" not in content and "=1+1" in content


@needs_lo
def test_cyrillic_8bit_text_survives_a_round_trip(step_ctx, tmp_path):
    src = tmp_path / "0.txt"
    src.write_bytes("Привет, мир\nВторая строка\n".encode("cp1251"))
    docx = _convert(step_ctx, src, "docx", tmp_path / "d")
    txt = _convert(step_ctx, docx, "txt", tmp_path / "t")
    assert txt.read_text(encoding="utf-8-sig").splitlines() == ["Привет, мир", "Вторая строка"]


@needs_lo
def test_external_references_and_webservice_become_errors(step_ctx, tmp_path):
    """Review H2 through the CSV path: the cells hold error values, never the file content."""
    other = tmp_path / "other-job"
    other.mkdir()
    secret = other / "secret.txt"
    secret.write_text("FILEX-SECRET-7f3a9c\n")
    src = linked_ods(tmp_path / "0.ods", secret, plain_ods(other / "s.ods", "FILEX-SECRET-7f3a9c"))
    text = _convert(step_ctx, src, "csv", tmp_path / "c").read_text(encoding="utf-8")
    hostname = Path("/etc/hostname").read_text().strip()
    assert "FILEX-SECRET" not in text and hostname not in text
    assert all(cell.strip('"').startswith(("#", "Err:")) for cell in text.split())
