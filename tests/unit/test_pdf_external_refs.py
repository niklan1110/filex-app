"""Sanitising PDF references to outside files before a non-pikepdf engine reads the PDF (task 64:
Calibre's PDF input goes through poppler, which may resolve /F external streams)."""

from pathlib import Path

import pytest

pikepdf = pytest.importorskip("pikepdf")

from filex.registry import StepError
from filex.tools._pdf import strip_external_refs

OTHER = pikepdf.String("/data/jobs/other/in/0.txt")


def hostile(path: Path) -> Path:
    """One page drawing an external image stream, a reference XObject, an OPI image and an
    external content stream (with /FFilter and a /FS /URL file spec)."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 200))
        page = pdf.pages[0]
        image = pdf.make_stream(
            b"",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=1,
            Height=1,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
            F=OTHER,
            FFilter=pikepdf.Name.FlateDecode,
            FDecodeParms=pikepdf.Dictionary(),
            OPI=pikepdf.Dictionary({"/1.3": pikepdf.Dictionary(F=OTHER)}),
        )
        form = pdf.make_stream(
            b"0 0 10 10 re f",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=[0, 0, 200, 200],
            Ref=pikepdf.Dictionary(F=OTHER, Page=0),
        )
        url = pikepdf.Dictionary(Type=pikepdf.Name.Filespec, FS=pikepdf.Name.URL, F=OTHER)
        external = pdf.make_stream(b"", F=url)
        draw = pdf.make_stream(b"q 50 0 0 50 10 10 cm /Im1 Do Q /Fm1 Do")
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image, Fm1=form))
        page.Contents = pikepdf.Array([external, draw])
        pdf.save(path)
    return path


def test_external_streams_reference_xobjects_and_opi_are_removed(tmp_path):
    out = strip_external_refs(hostile(tmp_path / "in.pdf"), tmp_path / "out.pdf", '"a.pdf"')
    assert out == tmp_path / "out.pdf"
    assert b"/data/jobs/other" not in out.read_bytes()
    with pikepdf.open(out) as pdf:
        streams = [o for o in pdf.objects if isinstance(o, pikepdf.Stream)]
        for s in streams:
            assert not {"/F", "/FFilter", "/FDecodeParms", "/OPI", "/Ref"} & set(s.keys())
        form = pdf.pages[0].Resources.XObject.Fm1
        assert form.read_bytes() == b"0 0 10 10 re f"  # the form's own content stays
        assert len(pdf.pages) == 1


def test_a_pdf_without_references_is_used_as_it_is(tmp_path):
    src = tmp_path / "in.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(src)
    assert strip_external_refs(src, tmp_path / "out.pdf", '"a.pdf"') == src
    assert not (tmp_path / "out.pdf").exists()


def test_an_unreadable_pdf_is_invalid_pdf(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(b"not a pdf")
    with pytest.raises(StepError) as e:
        strip_external_refs(src, tmp_path / "out.pdf", '"a.pdf"')
    assert e.value.code == "INVALID_PDF"
