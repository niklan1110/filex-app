"""The shared page-range parser used by pdf.split, pdf.rotate, pdf.extract-pages and
pdf.remove-pages (filex/tools/_pdf.py)."""

import pytest
from pydantic import ValidationError

from filex.registry import StepError, ToolOptions
from filex.tools._pdf import Pages, RequiredPages, parse_ranges, select


class Opts(ToolOptions):
    pages: Pages = ""


class Required(ToolOptions):
    pages: RequiredPages


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5", [("5", 5, 5)]),
        ("1-3", [("1-3", 1, 3)]),
        ("8-", [("8-", 8, None)]),
        ("-4", [("-4", 1, 4)]),
        ("3-3", [("3-3", 3, 3)]),
        (" 1 - 3 , 5,8 - ", [("1-3", 1, 3), ("5", 5, 5), ("8-", 8, None)]),
        ("7,2", [("7", 7, 7), ("2", 2, 2)]),  # order as written
        ("", []),
        ("   ", []),
    ],
)
def test_parses_pages_and_ranges(text, expected):
    assert parse_ranges(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1-3, x", '"x" is not a page or a range. Use page numbers and ranges, like 1-3, 5, 8-.'),
        ("last", '"last" is not a page or a range.'),
        ("1,,2", "There is an empty entry."),
        ("1-3,", "There is an empty entry."),
        ("0", '"0": pages are numbered from 1.'),
        ("3-0", '"3-0": pages are numbered from 1.'),
        ("5-3", '"5-3" goes backwards. Write it as 3-5.'),
        ("1--3", '"1--3" is not a page or a range.'),
        ("-", '"-" is not a page or a range.'),
        ("1.5", '"1.5" is not a page or a range.'),
    ],
)
def test_bad_ranges_name_the_entry_as_an_options_error(text, message):
    with pytest.raises(ValidationError) as e:
        Opts(pages=text)
    (err,) = e.value.errors()
    assert err["loc"] == ("pages",)
    assert err["msg"].startswith(message)


def test_required_pages_reject_blank_text():
    with pytest.raises(ValidationError) as e:
        Required(pages=" ")
    assert e.value.errors()[0]["msg"].startswith("Enter at least one page.")


def test_too_long_text_is_rejected():
    with pytest.raises(ValidationError):
        Opts(pages="1," * 101)


def test_schema_marks_a_page_range_field():
    prop = Opts.model_json_schema(by_alias=True)["properties"]["pages"]
    assert prop["format"] == "page-range"
    assert prop["maxLength"] == 200


def test_select_clips_ranges_at_the_last_page():
    ranges = parse_ranges("2-4, 3-, -20, 1")
    assert [list(r) for r in select(ranges, 5, "The file")] == [
        [1, 2, 3],
        [2, 3, 4],
        [0, 1, 2, 3, 4],
        [0],
    ]


def test_select_rejects_a_range_that_starts_past_the_end():
    with pytest.raises(StepError) as e:
        select(parse_ranges("1, 6-8"), 5, '"a.pdf"')
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == '"a.pdf" has 5 pages, so "6-8" selects no page.'
