"""The conversion planner (ADR-0005, plan task 36) over the real tool registry.

The golden routes pin today's choices: a new edge or cost that changes a popular route shows up
here as a test diff (ADR-0005 "Consequences")."""

import dataclasses

import pytest

from filex.api.deps import ApiError
from filex.api.routes import convert_route
from filex.planner import MAX_STEPS, route, routes_from
from filex.registry import Edge, Files, discover

TOOLS = discover()
ALL = set(TOOLS)


def without(*groups):
    return {i for i, t in TOOLS.items() if t.group not in groups}


def hops(src, dst, online=None, files=1):
    found = route(TOOLS.values(), src if isinstance(src, list) else [src], dst, online, files)
    return None if found is None else [(h.tool, h.dst) for h in found.hops]


GOLDEN = {
    # plan task 36 acceptance
    ("docx", "pdf"): [("office.to-pdf", "pdf")],
    ("pdf", "docx"): [("pdf.to-docx", "docx")],  # cost 5; Calibre's PDF-source edge costs 6
    ("xlsx", "ods"): [("office.convert", "ods")],
    ("pptx", "pdf"): [("office.to-pdf", "pdf")],
    ("jpg", "pdf"): [("image.to-pdf", "pdf")],
    ("pdf", "png"): [("pdf.to-image", "png")],
    ("docx", "txt"): [("markup.convert", "txt")],
    ("docx", "epub"): [("markup.convert", "epub")],
    ("epub", "pdf"): [("ebook.convert", "pdf")],
    ("epub", "mobi"): [("ebook.convert", "mobi")],
    ("fb2", "azw3"): [("ebook.convert", "azw3")],
    ("md", "epub"): [("markup.convert", "epub")],
    ("md", "mobi"): [("markup.convert", "epub"), ("ebook.convert", "mobi")],
    ("html", "epub"): [("ebook.convert", "epub")],
    ("djvu", "pdf"): [("djvu.to-pdf", "pdf")],
    ("cbr", "pdf"): [("comic.to-pdf", "pdf")],
    ("pdf", "cbz"): [("pdf.to-cbz", "cbz")],
    # more popular pairs
    ("fb2", "docx"): [("markup.convert", "docx")],
    ("md", "pdf"): [("markup.convert", "epub"), ("ebook.convert", "pdf")],
    ("djvu", "txt"): [("djvu.to-pdf", "pdf"), ("pdf.to-text", "txt")],
    ("jpg", "txt"): [("image.to-pdf", "pdf"), ("pdf.to-text", "txt")],
    ("xlsx", "csv"): [("office.convert", "csv")],
    ("cbz", "pdf"): [("comic.to-pdf", "pdf")],
    ("pdf", "txt"): [("pdf.to-text", "txt")],
    ("pptx", "epub"): [("office.to-pdf", "pdf"), ("ebook.convert", "epub")],
}


@pytest.mark.parametrize(("src", "dst"), list(GOLDEN), ids=[f"{a}-{b}" for a, b in GOLDEN])
def test_golden_route_with_every_group_online(src, dst):
    assert hops(src, dst, ALL) == GOLDEN[(src, dst)]


def test_pptx_to_epub_has_one_lossy_step_and_costs_more():
    found = route(TOOLS.values(), ["pptx"], "epub", ALL)
    assert [h.lossy for h in found.hops] == [False, True]
    assert found.cost == 1 + (1 + 4 + 1)  # pdf -> epub: lossy, plus Calibre's PDF-source cost


def test_only_online_tools_count():
    assert hops("epub", "mobi", without("ebook")) is None
    assert hops("md", "mobi", without("ebook")) is None
    assert hops("pptx", "pdf", without("office")) is None
    # Calibre still reads Word documents when LibreOffice is off
    assert hops("docx", "pdf", without("office")) == [("ebook.convert", "pdf")]
    # pdf.to-docx is core: Word from PDF needs no profile
    assert hops("pdf", "docx", without("office", "ebook", "ocr")) == [("pdf.to-docx", "docx")]
    assert hops("pdf", "docx", ALL - {"pdf.to-docx"}) == [("ebook.convert", "docx")]
    # with no profiles, only core routes
    assert hops("jpg", "txt", without("office", "ebook", "ocr")) == GOLDEN[("jpg", "txt")]


def test_no_route_names_the_profile_that_would_create_one():
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["epub"], "mobi", without("ebook"))
    assert e.value.code == "NO_ROUTE" and e.value.details["profiles"] == ["ebook"]
    assert "COMPOSE_PROFILES" in e.value.message
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["docx"], "pdf", without("office", "ebook"))
    assert e.value.details["profiles"] == ["office"]


def test_no_route_at_all_names_no_profile():
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["pdf"], "heic", ALL)
    assert e.value.code == "NO_ROUTE" and e.value.details["profiles"] == []


def test_same_format_is_no_route():
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["pdf"], "pdf", ALL)
    assert e.value.code == "NO_ROUTE"


def test_unknown_format_is_rejected():
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["nope"], "pdf", ALL)
    assert e.value.code == "UNKNOWN_FORMAT"


def test_more_files_than_a_step_takes():
    assert hops("djvu", "pdf", ALL, files=11) is None  # djvu.to-pdf takes 10
    with pytest.raises(ApiError) as e:
        convert_route(TOOLS, ["djvu"], "pdf", ALL, files=11)
    assert e.value.code == "TOO_MANY_FILES" and e.value.details["max"] == 10


def test_mixed_formats_share_one_first_tool():
    assert hops(["jpg", "png"], "pdf", ALL) == [("image.to-pdf", "pdf")]
    assert hops(["jpg", "png"], "txt", ALL) == GOLDEN[("jpg", "txt")]
    assert hops(["docx", "md"], "epub", ALL) == [("markup.convert", "epub")]
    assert hops(["pdf", "docx"], "pdf", ALL) is None  # a file already PDF cannot share a route


def test_routes_never_exceed_the_step_cap():
    for src in {e.src for t in TOOLS.values() for e in t.converts}:
        for found in routes_from(TOOLS.values(), [src], ALL).values():
            assert 1 <= len(found.hops) <= MAX_STEPS


def _chain(n):
    """Converters f0 -> f1 -> ... -> fn over real format ids, one tool per hop."""
    fmts = ["md", "html", "epub", "fb2", "mobi", "azw3"]
    base = TOOLS["markup.convert"]
    return [
        dataclasses.replace(base, id=f"t.{i}", converts=(Edge(fmts[i], fmts[i + 1]),))
        for i in range(n)
    ]


def test_five_hops_is_too_many():
    assert route(_chain(4), ["md"], "mobi") is not None
    assert route(_chain(5), ["md"], "azw3") is None


def test_ties_break_on_steps_then_tool_id():
    base = TOOLS["markup.convert"]
    b = dataclasses.replace(base, id="b.tool", converts=(Edge("md", "epub"),))
    a = dataclasses.replace(base, id="a.tool", converts=(Edge("md", "epub"),))
    slow = dataclasses.replace(base, id="b.slow", converts=(Edge("md", "epub", cost=1),))
    two = [
        dataclasses.replace(base, id="0.first", converts=(Edge("md", "html"),)),
        dataclasses.replace(base, id="0.second", converts=(Edge("html", "epub"),)),
    ]
    assert [h.tool for h in route([b, a], ["md"], "epub").hops] == ["a.tool"]
    # both cost 2; the direct route has fewer steps, although "0.first" sorts first
    assert [h.tool for h in route([slow, *two], ["md"], "epub").hops] == ["b.slow"]


def test_lossy_edges_cost_more_than_a_detour():
    base = TOOLS["markup.convert"]
    lossy = dataclasses.replace(base, id="a.lossy", converts=(Edge("md", "epub", lossy=True),))
    detour = [
        dataclasses.replace(base, id="z.one", converts=(Edge("md", "html"),)),
        dataclasses.replace(base, id="z.two", converts=(Edge("html", "epub"),)),
    ]
    assert [h.tool for h in route([lossy, *detour], ["md"], "epub").hops] == ["z.one", "z.two"]


def test_tools_taking_too_few_files_are_skipped():
    base = TOOLS["markup.convert"]
    pair = dataclasses.replace(
        base, id="t.pair", converts=(Edge("md", "epub"),), files=Files(min=2, max=5)
    )
    assert route([pair], ["md"], "epub", files=1) is None
    assert route([pair], ["md"], "epub", files=2) is not None
