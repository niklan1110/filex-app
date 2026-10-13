"""sniff() on hostile or broken input: it never raises and never parses an unbounded zip."""

import io
import random
import struct
import zipfile
from pathlib import Path

import pytest

from filex import formats
from filex.formats import sniff

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
ODT_MIME = b"application/vnd.oasis.opendocument.text"


def odf_zip(mimetype_compress=zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", ODT_MIME, compress_type=mimetype_compress)
        zf.writestr("content.xml", b"<office:document-content/>" * 4, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def zip_with_entries(n: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(n):
            zf.writestr(f"{i}.txt", b"")
    return buf.getvalue()


def as_zip64(data: bytes, declared: int | None = None) -> bytes:
    """Rewrite a small zip's end record as ZIP64: 16/32-bit fields maxed, real values in the
    ZIP64 record (what writers emit past 65535 entries or 4 GiB)."""
    at = data.rfind(b"PK\x05\x06")
    count, cd_size, cd_offset = struct.unpack("<HII", data[at + 10 : at + 20])
    count = count if declared is None else declared
    rec = b"PK\x06\x06" + struct.pack(
        "<QHHIIQQQQ", 44, 45, 45, 0, 0, count, count, cd_size, cd_offset
    )
    loc = b"PK\x06\x07" + struct.pack("<IQI", 0, at, 1)
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return data[:at] + rec + loc + eocd


def write(tmp_path: Path, data: bytes, name: str = "x.bin") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- H1: malformed input returns None or a format, never an exception


def mobi_head() -> bytes:
    return (CORPUS / "ebook/book.mobi").read_bytes()[:4096]


@pytest.mark.parametrize(
    "label,sample",
    [
        ("odf-zip", odf_zip),
        ("odf-zip-deflated-mimetype", lambda: odf_zip(zipfile.ZIP_DEFLATED)),
        ("odf-zip-lzma-mimetype", lambda: odf_zip(zipfile.ZIP_LZMA)),
        ("ole2-xls", lambda: (CORPUS / "office/legacy.xls").read_bytes()),
        ("mobi-header", mobi_head),
    ],
)
def test_byte_mutations_never_raise(tmp_path, label, sample):
    rng = random.Random(f"filex-{label}")  # seeded: failures reproduce
    original = sample()
    p = tmp_path / "fuzz.bin"
    for _ in range(300):
        data = bytearray(original)
        for _ in range(rng.randint(1, 8)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.2:
            del data[rng.randrange(len(data)) :]
        p.write_bytes(bytes(data))
        result = sniff(p, "fuzz.cbz")
        assert result is None or result in formats.FORMATS


def test_short_mobi_does_not_raise(tmp_path):
    p = write(tmp_path, b"\0" * 60 + b"BOOKMOBI" + b"\0" * 6)  # 74 bytes, no record list
    assert sniff(p) is None


def test_encrypted_mimetype_entry_does_not_raise(tmp_path):
    data = bytearray(odf_zip())
    for sig, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(sig)  # first local header and first central header: the mimetype
        data[at + flag_at] |= 1
    assert sniff(write(tmp_path, bytes(data))) == "zip"  # not ODF, and never opened


@pytest.mark.parametrize("method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2])
def test_corrupt_compressed_mimetype_does_not_raise(tmp_path, method):
    data = bytearray(odf_zip(method))
    start = data.find(b"mimetype") + len("mimetype")
    data[start : start + 8] = b"\xff" * 8  # garbage in the compressed stream
    assert sniff(write(tmp_path, bytes(data))) == "zip"  # not ODF, and never decompressed


def test_odf_mimetype_must_be_stored(tmp_path):
    # ODF and EPUB require it; a deflated one is a plain zip and no decompressor runs.
    assert sniff(write(tmp_path, odf_zip(zipfile.ZIP_STORED))) == "odt"
    assert sniff(write(tmp_path, odf_zip(zipfile.ZIP_DEFLATED))) == "zip"


# --- H2: the entry count is checked before zipfile builds its directory


@pytest.fixture
def forbid_zipfile(monkeypatch):
    """Call it once the test has built its zips; returns the list of attempted opens."""
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("zipfile.ZipFile must not be opened")

    def arm():
        monkeypatch.setattr(zipfile, "ZipFile", refuse)
        return calls

    return arm


def test_zip_over_the_entry_cap_is_rejected_without_opening_it(tmp_path, forbid_zipfile):
    corpus = CORPUS / "generated/many_entries.cbz"
    if corpus.is_file():
        p = corpus
    else:
        p = write(tmp_path, zip_with_entries(formats.MAX_ARCHIVE_ENTRIES + 1), "many.cbz")
    opened = forbid_zipfile()
    assert sniff(p, "many_entries.cbz") is None
    assert opened == []


def test_entry_cap_counts_headers_not_the_declared_count(tmp_path, monkeypatch, forbid_zipfile):
    # zipfile walks the directory by its byte size and ignores the declared count.
    monkeypatch.setattr(formats, "MAX_ARCHIVE_ENTRIES", 20)
    data = bytearray(zip_with_entries(30))
    at = data.rfind(b"PK\x05\x06")
    data[at + 8 : at + 12] = struct.pack("<HH", 1, 1)
    p = write(tmp_path, bytes(data))
    opened = forbid_zipfile()
    assert sniff(p) is None
    assert opened == []


def test_entry_count_reads_zip64_records(tmp_path, monkeypatch):
    monkeypatch.setattr(formats, "MAX_ARCHIVE_ENTRIES", 20)
    small = as_zip64(zip_with_entries(3))
    assert zipfile.ZipFile(io.BytesIO(small)).namelist() == ["0.txt", "1.txt", "2.txt"]
    assert sniff(write(tmp_path, small, "small.zip")) == "zip"
    assert sniff(write(tmp_path, as_zip64(zip_with_entries(30)), "big.zip")) is None
    lying = as_zip64(zip_with_entries(30), declared=1)
    assert sniff(write(tmp_path, lying, "lying.zip")) is None


def test_zip_at_the_cap_is_still_classified(tmp_path, monkeypatch):
    monkeypatch.setattr(formats, "MAX_ARCHIVE_ENTRIES", 20)
    assert sniff(write(tmp_path, zip_with_entries(20), "a.cbz"), "a.cbz") == "cbz"
    assert sniff(write(tmp_path, zip_with_entries(21), "b.cbz"), "b.cbz") is None


def test_cap_defaults_to_the_spec_value():
    assert formats.MAX_ARCHIVE_ENTRIES == 5000  # spec 5.1, FILEX_MAX_ARCHIVE_ENTRIES
