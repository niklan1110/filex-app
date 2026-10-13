"""Engine probes (review H4 fix 3): version parsing and minimums, without the engines."""

import shutil
import sys

import pytest

from filex.worker.runner import ENGINES, Probe, probe


def _fake(min_version: str, output: str) -> Probe:
    return Probe(min_version, (sys.executable, "-c", f"print({output!r})"))


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("LibreOffice 25.2.3.2 520(Build:2)", "25.2.3.2"),
        ("LibreOffice 24.8.0.3 0bdf1299c94fe897b119f97f3c613e9dca6be583", "24.8.0.3"),
        ("LibreOffice 7.4.7.2 40(Build:2)", None),  # Debian 12's own: no DisableActiveContent
    ],
)
def test_libreoffice_minimum_is_24_8(output, expected):
    p = ENGINES["libreoffice"]
    assert p.cmd[0] == "soffice"
    assert probe(_fake(p.min_version, output)) == expected


def test_missing_binary_fails_the_probe():
    assert probe(Probe("1", ("filex-no-such-engine", "--version"))) is None


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_installed_libreoffice_passes_its_probe():
    assert probe(ENGINES["libreoffice"]) is not None


@pytest.mark.parametrize(
    ("engine", "module"),
    [
        ("img2pdf", "img2pdf"),
        ("pillow", "PIL"),
        ("pypdfium2", "pypdfium2"),
        ("pymupdf", "pymupdf"),
        ("python-docx", "docx"),
    ],
)
def test_python_engine_probes_report_the_installed_version(engine, module):
    pytest.importorskip(module)
    assert probe(ENGINES[engine]) is not None


def test_old_pillow_fails_its_probe():  # 10.3 fixes CVE-2024-28219
    assert probe(_fake(ENGINES["pillow"].min_version, "10.2.0")) is None


@pytest.mark.parametrize(
    ("engine", "output", "expected"),
    [  # real --version output of the worker-ocr image, then one too old
        ("tesseract", "tesseract 5.5.0\n leptonica-1.84.1\n", "5.5.0"),
        ("tesseract", "tesseract 4.1.1\n leptonica-1.82.0\n", None),
        ("ocrmypdf", "17.13.0", "17.13.0"),
        ("ocrmypdf", "16.7.0", None),
        ("unpaper", "7.0.0", "7.0.0"),
        ("unpaper", "6.1", None),
        ("opencv", "5.0.0", "5.0.0"),
        ("opencv", "4.6.0", None),
    ],
)
def test_ocr_engine_minimums(engine, output, expected):
    assert probe(_fake(ENGINES[engine].min_version, output)) == expected


@pytest.mark.parametrize(
    ("engine", "output", "expected"),
    [  # real output of the worker-ebook image, then one too old
        ("calibre", "ebook-convert (calibre 9.16.0)\nCreated by: Kovid Goyal\n", "9.16.0"),
        ("calibre", "ebook-convert (calibre 8.5.0)\n", None),  # Debian 13's package
        ("pandoc", "pandoc 3.11\nFeatures: +server +lua\n", "3.11"),
        ("pandoc", "pandoc 3.1.11.1\n", None),  # Debian 13's: --sandbox cannot write EPUB
        ("djvulibre", "DDJVU --- DjVuLibre-3.5.28\nDjVu file to image converter.\n", "3.5.28"),
        ("djvulibre", "DDJVU --- DjVuLibre-3.5.27\n", None),
        ("libarchive", "3.7.4\n", "3.7.4"),  # Debian 13
        ("libarchive", "3.6.2\n", None),
    ],
)
def test_ebook_engine_minimums(engine, output, expected):
    assert probe(_fake(ENGINES[engine].min_version, output)) == expected


def test_ebook_probe_commands():
    assert ENGINES["calibre"].cmd == ("ebook-convert", "--version")
    assert ENGINES["pandoc"].cmd[0] == "pandoc"
    assert ENGINES["djvulibre"].cmd[-1] == "ddjvu --help 2>&1"  # its banner is on stderr
    assert ENGINES["libarchive"].cmd[:2] == (sys.executable, "-c")
    assert "libarchive" in ENGINES["libarchive"].cmd[-1]


def test_reportlab_probe_reports_the_installed_version():
    pytest.importorskip("reportlab")
    assert probe(ENGINES["reportlab"]) is not None


@pytest.mark.parametrize(("output", "expected"), [("4.4.4", "4.4.4"), ("3.6.13", None)])
def test_reportlab_minimum_is_4(output, expected):
    assert probe(_fake(ENGINES["reportlab"].min_version, output)) == expected


@pytest.mark.parametrize(
    ("engine", "module"), [("pyhanko", "pyhanko"), ("cryptography", "cryptography")]
)
def test_signing_engine_probes_report_the_installed_version(engine, module):
    pytest.importorskip(module)
    assert probe(ENGINES[engine]) is not None


@pytest.mark.parametrize(
    ("engine", "output", "expected"),
    [
        ("pyhanko", "0.37.0", "0.37.0"),
        ("pyhanko", "0.25.3", None),
        ("cryptography", "50.0.2", "50.0.2"),
        ("cryptography", "41.0.7", None),  # Debian 12's python3-cryptography
    ],
)
def test_signing_engine_minimums(engine, output, expected):
    assert probe(_fake(ENGINES[engine].min_version, output)) == expected


def test_libarchive_probe_reports_the_installed_version():
    try:
        import libarchive  # noqa: F401
    except (ImportError, OSError, TypeError):  # no package, or no libarchive.so (Windows)
        pytest.skip("needs libarchive (worker-ebook)")
    assert probe(ENGINES["libarchive"]) is not None
