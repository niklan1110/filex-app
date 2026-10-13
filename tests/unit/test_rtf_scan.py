"""RTF pre-scan of the office tools (task 64: LibreOffice follows INCLUDEPICTURE in RTF)."""

import pytest

from filex.registry import StepError
from filex.tools.office import _rtf


def rtf(body: str) -> bytes:
    return ("{\\rtf1\\ansi{\\fonttbl{\\f0 Helvetica;}}\\f0 Visible.\\par\n" + body + "}").encode(
        "latin-1"
    )


def field(instr: str) -> str:
    return "{\\field{\\*\\fldinst " + instr + "}{\\fldrslt cached}}"


def refused(data: bytes) -> str:
    with pytest.raises(StepError) as e:
        _rtf.check(data)
    assert e.value.code == "INVALID_INPUT"
    return e.value.message


@pytest.mark.parametrize(
    "body",
    [
        field('INCLUDEPICTURE "file:///data/jobs/x/in/1.png" \\\\d'),
        field('INCLUDEPICTURE "../../jobs/x/in/1.png"'),
        field('INCLUDETEXT "/data/jobs/x/in/0.txt"'),
        field('INCLUDE "/etc/hostname"'),  # Word 6 name of INCLUDETEXT
        field('IMPORT "/data/jobs/x/in/1.png"'),  # old name of INCLUDEPICTURE
        field('LINK Excel.Sheet.8 "/data/jobs/x/in/2.xls" "Sheet1!R1C1" \\\\a'),
        field('DDE Excel "/data/x.xls" R1C1'),
        field('DDEAUTO Excel "/data/x.xls" R1C1'),
        field('DATABASE \\\\d "/data/x.mdb" \\\\s "select *"'),
        field('RD "/data/other.doc"'),
        field('HYPERLINK "file:///etc/hostname"'),
        field('HYPERLINK "/data/jobs/x/in/3.pdf"'),
        field('HYPERLINK "..\\\\..\\\\jobs\\\\x\\\\in\\\\3.pdf"'),
        field('HYPERLINK "C:\\\\Users\\\\x.pdf"'),
        '{\\*\\fldinst INCLUDEPICTURE "x.png"}',  # no \\field around it
        '{\\field{\\fldinst INCLUDEPICTURE "x.png"}}',  # no \\* either
        "{\\shp{\\*\\shpinst{\\sp{\\sn pibName}{\\sv /data/jobs/x/in/1.png}}}}",
        "{\\shp{\\*\\shpinst{\\sp{\\sn fillBlipName}{\\sv /data/x.png}}}}",
        "{\\object\\objautlink{\\*\\objclass Excel.Sheet.8}{\\*\\objdata 0105}}",
        "{\\object\\objlink{\\*\\objdata 0105}}",
    ],
)
def test_fields_and_links_to_outside_files_are_refused(body):
    assert refused(rtf(body)) == (
        "This RTF links to files outside the document, which FileX does not allow."
    )


@pytest.mark.parametrize(
    "instr",
    [
        '\\\'49NCLUDEPICTURE "x.png"',  # hex escape
        '\\\'69ncludepicture "x.png"',  # lower case
        '\\u73?NCLUDEPICTURE "x.png"',  # unicode escape, one fallback char
        '\\uc0\\u73 NCLUDEPICTURE "x.png"',  # no fallback char
        '\\uc2\\u73 ??NCLUDEPICTURE "x.png"',
        'INCL{\\v}UDEPICTURE "x.png"',  # empty group in the name
        'INCL{UDE}PICTURE "x.png"',
        'INCL\\b UDEPICTURE "x.png"',  # control word (its delimiting space is not text)
        'INCL\r\nUDEPICTURE "x.png"',  # line breaks are not text in RTF
        '{\\*\\junk XX}INCLUDEPICTURE "x.png"',  # a skipped destination before the name
        '{\\*\\junk XX}LINK Excel "x.xls"',
        '{\\v hidden}LINK Excel "x.xls"',
        '  \\tab LINK Excel "x.xls"',
        'IF 1 = 1 "INCLUDETEXT x"',
        '{\\field{\\*\\fldinst INCLUDEPICTURE "x.png"}}',  # nested field
        '\\bin3 }}}INCLUDEPICTURE "x.png"',  # \\bin data is skipped like LibreOffice does
        'HYPERLINK "fi\\\'6ce:///etc/hostname"',
    ],
)
def test_obfuscated_field_instructions_are_refused(instr):
    refused(rtf(field(instr)))


def test_negative_unicode_escapes_wrap_around():
    # \u-65463 is U+0049 'I' written as a signed 16-bit number.
    refused(rtf(field('\\u-65463?NCLUDEPICTURE "x.png"')))


@pytest.mark.parametrize(
    "body",
    [
        "Plain text that mentions INCLUDEPICTURE, LINK and file:///etc/hostname.\\par",
        field("PAGE"),
        field("NUMPAGES \\\\* MERGEFORMAT"),
        field('HYPERLINK "https://www.linkedin.com/in/example"'),
        field('HYPERLINK \\\\l "_Toc123"'),
        field('HYPERLINK "report.pdf"'),
        field("PAGEREF _Toc123 \\\\h"),
        field('TC "Important links" \\\\l 1'),
        field('XE "Database import"'),
        field('DATE \\\\@ "d MMMM yyyy"'),
        field("REF _Ref_link_1 \\\\h"),
        (
            "{\\shp{\\*\\shpinst{\\sp{\\sn wzDescription}{\\sv A linked image}}"
            "{\\sp{\\sn pib}{\\sv {\\pict\\pngblip 89504e47}}}}}"
        ),
        "{\\pict\\pngblip\\picw10\\pich10 89504e470d0a1a0a}",
        "{\\*\\bkmkstart link}{\\*\\bkmkend link}",
    ],
)
def test_ordinary_documents_pass(body):
    _rtf.check(rtf(body))


def test_absurd_nesting_is_refused():
    msg = refused(rtf("{" * 5000 + "x" + "}" * 5000 + field("PAGE")))
    assert "nested" in msg


def nested_fields(n: int) -> str:
    """``n`` field instructions, each inside the one before (IF fields nest like this)."""
    return "{\\field{\\*\\fldinst IF " * n + "x" + "}}" * n


def test_too_many_nested_field_instructions_are_refused():
    # Security review L-4: each open instruction collects every character after it, so the
    # work grows with the nesting; past MAX_FIELDS the file is not worth deciding.
    assert "nested" in refused(rtf(nested_fields(_rtf.MAX_FIELDS + 1)))


def test_nested_field_instructions_up_to_the_cap_pass():
    _rtf.check(rtf(nested_fields(_rtf.MAX_FIELDS)))


def test_deep_nesting_without_fields_is_not_parsed():
    _rtf.check(rtf("{" * 5000 + "x" + "}" * 5000))


def test_an_unclosed_field_at_the_end_is_still_checked():
    refused(b'{\\rtf1{\\field{\\*\\fldinst INCLUDEPICTURE "x.png"')


def test_a_bin_length_past_the_end_is_safe():
    _rtf.check(b"{\\rtf1{\\field{\\*\\fldinst PAGE}}\\bin99999 ab}")
