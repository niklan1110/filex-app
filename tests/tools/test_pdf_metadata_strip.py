"""pdf.metadata-strip (plan task 30, review M8): every item the help text lists is removed, the
output is re-checked, and a strip that leaves something behind fails the job.

The corpus' metadata.pdf has Info and XMP only, so ``loaded`` adds the rest of the known hidden
data to a copy: attachments, scripts, actions, piece info, thumbnails, page XMP, comment authors.
"""

import shutil
import subprocess
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pikepdf import Array, Dictionary, Name

import filex.tools.security.pdf_metadata_strip as strip_mod
from filex.registry import StepError
from filex.tools.security.pdf_metadata_strip import Options, run

AUTHOR = "Jane Example"
SECRET = b"attached secret payroll"
SCRIPT = "var trackingBeacon = 1;"
SPIDER = "https://intranet.example/private"
NM = "author-guid-4f2a"


def _js(pdf: pikepdf.Pdf) -> pikepdf.Object:
    return pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=SCRIPT))


@pytest.fixture
def loaded(tmp_path) -> Path:
    path = tmp_path / "loaded.pdf"
    with pikepdf.open(PDF / "metadata.pdf") as pdf:
        spec = pikepdf.AttachedFileSpec(pdf, SECRET, mime_type="text/plain")
        pdf.attachments["payroll.txt"] = spec
        pdf.Root.Names.JavaScript = Dictionary(Names=Array(["init", _js(pdf)]))
        pdf.Root.OpenAction = _js(pdf)
        pdf.Root.AA = Dictionary(WC=_js(pdf))
        pdf.Root.AF = Array([spec.obj])
        pdf.Root.SpiderInfo = Dictionary(V=1.0, C=Array([Dictionary(URL=pikepdf.String(SPIDER))]))
        pdf.Root.Perms = Dictionary(Custom=pikepdf.String(AUTHOR))
        thread = pdf.make_indirect(
            Dictionary(Type=Name.Thread, I=Dictionary(Title="Story", Author=pikepdf.String(AUTHOR)))
        )
        pdf.Root.Threads = Array([thread])
        page = pdf.pages[0].obj
        page.AA = Dictionary(O=_js(pdf))
        page.PieceInfo = Dictionary(Editor=Dictionary(Private=pikepdf.String(AUTHOR)))
        page.LastModified = pikepdf.String("D:20240101000000")
        page.Thumb = pdf.make_stream(
            b"\x00" * 12, Width=2, Height=2, ColorSpace=Name.DeviceRGB, BitsPerComponent=8
        )
        page.Metadata = pdf.make_stream(
            f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><dc:creator>{AUTHOR}</dc:creator>'
            "</rdf:RDF></x:xmpmeta>".encode(),
            Type=Name.Metadata,
            Subtype=Name.XML,
        )
        comment = Dictionary(
            Type=Name.Annot,
            Subtype=Name.Text,
            Rect=[50, 50, 70, 70],
            Contents=pikepdf.String("Please check the totals"),
            T=pikepdf.String(AUTHOR),
            M=pikepdf.String("D:20240101000000"),
            NM=pikepdf.String(NM),
        )
        link = Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[80, 80, 120, 100], A=_js(pdf))
        attachment = Dictionary(
            Type=Name.Annot, Subtype=Name.FileAttachment, Rect=[130, 130, 150, 150], FS=spec.obj
        )
        page.Annots = pdf.make_indirect(
            Array([pdf.make_indirect(a) for a in (comment, link, attachment)])
        )
        pdf.save(path)
    return path


def strip(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def raw(path: Path, tmp_path: Path) -> bytes:
    """The file's bytes without object streams, plus every stream's decoded data, to grep
    (QDF mode leaves embedded-file streams compressed)."""
    plain = tmp_path / "plain.pdf"
    with pikepdf.open(path) as pdf:
        pdf.save(plain, qdf=True, object_stream_mode=pikepdf.ObjectStreamMode.disable)
        streams = [o.read_bytes() for o in pdf.objects if isinstance(o, pikepdf.Stream)]
    return b"\n".join([plain.read_bytes(), *streams])


def test_loaded_file_really_has_the_hidden_data(loaded, tmp_path):
    data = raw(loaded, tmp_path)
    assert AUTHOR.encode() in data and SECRET in data and SCRIPT.encode() in data


def test_every_listed_item_is_removed(step_ctx, loaded, tmp_path):
    out = strip(step_ctx, loaded)
    with pikepdf.open(out) as pdf, pikepdf.open(loaded) as before:
        assert "/Info" not in pdf.trailer
        assert "/Metadata" not in pdf.Root
        assert bytes(pdf.trailer.ID[0]) != bytes(before.trailer.ID[0])
        assert len(pdf.attachments) == 0
        assert "/JavaScript" not in pdf.Root.Names
        for key in ("/OpenAction", "/AA", "/AF", "/SpiderInfo", "/Perms"):
            assert key not in pdf.Root, key
        page = pdf.pages[0].obj
        for key in ("/AA", "/PieceInfo", "/LastModified", "/Thumb", "/Metadata"):
            assert key not in page, key
        kinds = [str(a.Subtype) for a in page.Annots]
        assert kinds == ["/Text", "/Link"]  # the file attachment is gone
        comment, link = page.Annots
        assert str(comment.Contents) == "Please check the totals"
        assert "/T" not in comment and "/M" not in comment and "/NM" not in comment
        assert "/I" not in pdf.Root.Threads[0]
        assert "/A" not in link
    data = raw(out, tmp_path)
    leaks = (AUTHOR.encode(), SECRET, SCRIPT.encode(), SPIDER.encode(), NM.encode())
    for leak in (*leaks, b"Example Corp", b"make_corpus"):
        assert leak not in data, leak
    assert step_ctx.warnings == []


def test_page_text_is_kept(step_ctx, loaded):
    with pikepdf.open(strip(step_ctx, loaded)) as pdf:
        assert b"Metadata sample - page 1 of 2" in pdf.pages[0].Contents.read_bytes()


def test_remove_comments_keeps_links_and_form_fields(step_ctx, loaded, tmp_path):
    with pikepdf.open(strip(step_ctx, loaded, remove_comments=True)) as pdf:
        assert [str(a.Subtype) for a in pdf.pages[0].Annots] == ["/Link"]
    (tmp_path / "f").mkdir()
    step_ctx.out_dir = tmp_path / "f"
    out = strip(step_ctx, PDF / "forms.pdf", remove_comments=True)
    with pikepdf.open(out) as pdf, pikepdf.open(PDF / "forms.pdf") as before:
        names = sorted(f.fully_qualified_name for f in pdf.acroform.fields)
        assert names == sorted(f.fully_qualified_name for f in before.acroform.fields)


def test_a_strip_that_leaves_data_fails_the_job(step_ctx, loaded, monkeypatch):
    def leaves_xmp(pdf, opts):
        xmp = pdf.Root.Metadata
        real(pdf, opts)
        pdf.Root.Metadata = xmp

    real = strip_mod._strip
    monkeypatch.setattr(strip_mod, "_strip", leaves_xmp)
    with pytest.raises(StepError) as e:
        run(step_ctx, loaded, Options())
    assert e.value.code == "STRIP_INCOMPLETE"
    assert "XMP metadata" in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []  # no half-clean file is returned


def _signature(pdf: pikepdf.Pdf) -> pikepdf.Object:
    return pdf.make_indirect(
        Dictionary(
            Type=Name.Sig,
            Name=pikepdf.String(AUTHOR),
            M=pikepdf.String("D:20240101000000"),
            ByteRange=Array([0, 0, 0, 0]),
            Contents=pikepdf.String(b"cert"),
        )
    )


def _sign(pdf: pikepdf.Pdf, sig_flags: int) -> None:
    field = Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, FT=Name.Sig, T="Sig1", Rect=[0, 0, 0, 0]
    )
    field.V = _signature(pdf)
    field = pdf.make_indirect(field)
    pdf.pages[0].obj.Annots = Array([field])
    pdf.Root.AcroForm = Dictionary(Fields=Array([field]))
    if sig_flags:
        pdf.Root.AcroForm.SigFlags = sig_flags


@pytest.mark.parametrize("sig_flags", [3, 0], ids=["sigflags", "signature-without-flag"])
def test_signed_input_is_refused(step_ctx, tmp_path, sig_flags):
    signed = tmp_path / "signed.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        _sign(pdf, sig_flags)
        pdf.save(signed)
    with pytest.raises(StepError) as e:
        run(step_ctx, signed, Options())
    assert e.value.code == "SIGNED_PDF"
    assert AUTHOR not in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


def _put_signature(pdf):
    pdf.Root.Leftover = _signature(pdf)


def _put_perms(pdf):
    pdf.Root.Perms = Dictionary(Custom=1)


def _put_spider(pdf):
    pdf.Root.SpiderInfo = Dictionary(V=1.0)


def _put_thread_info(pdf):
    pdf.Root.Threads = Array([Dictionary(I=Dictionary(Author=pikepdf.String(AUTHOR)))])


def _put_bead_thread_info(pdf):  # a typed thread reached only through a page
    pdf.pages[0].obj.Leftover = Dictionary(Type=Name.Thread, I=Dictionary(Title="x"))


def _put_annotation_name(pdf):
    pdf.pages[0].obj.Annots[0].NM = pikepdf.String(NM)


@pytest.mark.parametrize(
    ("leave", "finding"),
    [
        (_put_signature, "signature value"),
        (_put_perms, "signature permissions"),
        (_put_spider, "web capture info"),
        (_put_thread_info, "article thread info"),
        (_put_bead_thread_info, "article thread info"),
        (_put_annotation_name, "annotation names"),
    ],
)
def test_self_check_catches_each_new_leftover(step_ctx, loaded, monkeypatch, leave, finding):
    real = strip_mod._strip

    def leaves(pdf, opts):
        real(pdf, opts)
        leave(pdf)

    monkeypatch.setattr(strip_mod, "_strip", leaves)
    with pytest.raises(StepError) as e:
        run(step_ctx, loaded, Options())
    assert e.value.code == "STRIP_INCOMPLETE"
    assert finding in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


def test_kept_document_id_fails_the_job(step_ctx, loaded, monkeypatch):
    real = strip_mod._strip

    def keeps_id(pdf, opts):
        ids = pdf.trailer.ID
        real(pdf, opts)
        pdf.trailer.ID = ids

    monkeypatch.setattr(strip_mod, "_strip", keeps_id)
    monkeypatch.setattr(strip_mod, "save", lambda pdf, target, what, **kw: pdf.save(target))
    with pytest.raises(StepError) as e:
        run(step_ctx, loaded, Options())
    assert "document ID" in e.value.message


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool is not installed")
def test_exiftool_finds_no_author_producer_or_xmp(step_ctx, loaded):
    out = strip(step_ctx, loaded)
    tags = subprocess.run(
        ["exiftool", "-a", "-G1", "-s", str(out)], capture_output=True, text=True, check=True
    ).stdout
    assert AUTHOR not in tags and "Producer" not in tags and "XMP" not in tags


def test_owner_restricted_input_is_cleaned_and_warns(step_ctx):
    with pikepdf.open(strip(step_ctx, PDF / "encrypted_owner_only.pdf")) as pdf:
        assert not pdf.is_encrypted and "/Info" not in pdf.trailer
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options())
    assert e.value.code == "INVALID_PDF"
