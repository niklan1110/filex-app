"""The @tool declaration API and registry, including every way a declaration can be wrong."""

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, Field, SecretStr, ValidationError
from tool_checks import (
    example_problems,
    module_level_imports,
    tool_test_path,
    undeclared_errors,
)

from filex.registry import (
    COMMON_ERRORS,
    RUNNER_ERRORS,
    Aux,
    Edge,
    Example,
    Files,
    Limits,
    Report,
    Secret,
    StepError,
    ToolDeclarationError,
    ToolOptions,
    discover,
    tool,
)


def declare(**overrides):
    """Declare a minimal valid tool with some fields overridden."""
    decl = {
        "id": "t.x",
        "title": "X",
        "summary": "Does x.",
        "category": "organize",
        "group": "core",
        "inputs": ["pdf"],
        "outputs": ["pdf"],
    }
    decl.update(overrides)
    return tool(**decl)(lambda ctx, src, opts: [])


def rejected(**overrides) -> str:
    with pytest.raises(ToolDeclarationError) as e:
        declare(**overrides)
    return str(e.value)


# --- the happy path


def test_dummy_tool_is_discovered_with_its_json_schema():
    tools = discover("sample_tools")
    assert list(tools) == ["sample.echo"]
    echo = tools["sample.echo"]
    assert echo.group == "core" and echo.inputs == ("pdf",) and echo.outputs == ("pdf",)
    assert echo.files == Files(min=1, max=2, each=True)
    assert echo.limits == Limits(timeout_s=10, memory_mb=64)
    assert echo.engines == ("imaginary-engine",) and echo.warnings == ("NOTHING_DONE",)
    schema = echo.options_schema
    assert schema["properties"]["mode"] == {
        "default": "COPY",
        "enum": ["COPY", "NOTHING"],
        "title": "Mode",
        "type": "string",
    }
    assert schema["properties"]["repeatCount"]["maximum"] == 3  # camelCase on the wire


def test_tool_stays_callable(tmp_path):
    echo = discover("sample_tools")["sample.echo"]
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.7")

    class Ctx:
        out_dir = tmp_path

    assert echo(Ctx(), src, echo.options()) == [tmp_path / "0.pdf"]


def test_options_accept_camel_case_and_reject_unknown_keys():
    opts = discover("sample_tools")["sample.echo"].options
    assert opts.model_validate({"repeatCount": 2}).repeat_count == 2
    with pytest.raises(ValidationError):
        opts.model_validate({"bogus": 1})


def test_tool_without_options_gets_an_empty_schema():
    t = declare()
    assert t.options is ToolOptions
    assert t.options_schema["properties"] == {}


def test_tool_with_no_input_files():
    t = declare(id="cert.create", inputs=[], outputs=["p12"], files=Files(min=0, max=0))
    assert t.inputs == () and t.files.max == 0


def test_converter_derives_inputs_and_outputs_from_edges():
    t = declare(
        inputs=[],
        outputs=[],
        category="convert",
        group="office",
        converts=[Edge("docx", "pdf"), Edge("odt", "pdf"), Edge("pdf", "epub", lossy=True)],
    )
    assert t.inputs == ("docx", "odt", "pdf")
    assert t.outputs == ("epub", "pdf")
    assert t.converts[2] == Edge("pdf", "epub", lossy=True, cost=0)


def test_secret_field_is_write_only_password():
    class Opts(ToolOptions):
        passphrase: SecretStr = Secret(title="Passphrase", min_length=4)

    prop = declare(options=Opts).options_schema["properties"]["passphrase"]
    assert prop["writeOnly"] is True and prop["format"] == "password"
    assert prop["title"] == "Passphrase" and prop["minLength"] == 4
    assert "passphrase" in declare(options=Opts).options_schema["required"]


def test_aux_slots_are_kept():
    files = Files(
        min=1,
        max=20,
        each=True,
        aux={"certificate": Aux(formats=["p12"], title="Certificate (.p12)", sensitive=True)},
    )
    t = declare(files=files)
    assert t.files.aux["certificate"].sensitive is True
    assert t.files.aux["certificate"].required is True


def test_report_marks_an_output_role():
    assert Report(Path("proof.json")).role == "REPORT"
    assert Report(Path("proof.json")).path == Path("proof.json")


def test_one_level_of_nested_options_is_allowed():
    class Margins(ToolOptions):
        top: int = Field(0, title="Top", ge=0, le=100)

    class Opts(ToolOptions):
        margins: Margins = Field(Margins(), title="Margins")
        target_mb: float | None = Field(None, title="Target size (MB)", gt=0, le=10_000)

    assert "margins" in declare(options=Opts).options_schema["properties"]


# --- generic checks fail on broken tools (plan task 2 acceptance)


def test_duplicate_id_fails_discovery():
    with pytest.raises(ToolDeclarationError, match="duplicate tool id 'bad.dup'"):
        discover("bad_tools")


def test_unknown_format_is_rejected():
    assert "unknown format 'pdff'" in rejected(inputs=["pdff"])
    assert "unknown format 'nope'" in rejected(outputs=["nope"])


def test_field_without_title_is_rejected():
    class Opts(ToolOptions):
        level: int = Field(1, ge=0, le=3)

    assert "level: needs a title" in rejected(options=Opts)


def test_module_level_engine_import_is_detected():
    assert module_level_imports("bad_tools.engine_import") == {"fake_engine"}
    assert module_level_imports("sample_tools.echo") == set()


def test_tool_without_test_file_is_detected():
    assert not tool_test_path("sample.echo").exists()
    assert tool_test_path("pdf.to-pdfa").name == "test_pdf_to_pdfa.py"


def _raises_a_code(ctx, src, opts):
    raise StepError("PAGE_TOO_BIG", "A page is too big.")


def test_undeclared_raised_error_code_is_detected():
    decl = {"title": "X", "summary": "x.", "category": "organize", "group": "core"}
    io = {"inputs": ["pdf"], "outputs": ["pdf"]}
    t = tool(id="t.raises", **decl, **io)(_raises_a_code)
    assert undeclared_errors(t) == {"PAGE_TOO_BIG"}
    t = tool(id="t.raises", **decl, **io, errors=["PAGE_TOO_BIG"])(_raises_a_code)
    assert undeclared_errors(t) == set()


def test_example_inputs_are_checked_against_the_corpus():
    assert example_problems(discover("sample_tools")["sample.echo"]) == []
    t = declare(
        examples=[Example(inputs=["office/report.docx"]), Example(inputs=["pdf/missing.pdf"])]
    )
    problems = example_problems(t)
    assert "report.docx is docx" in problems[0] and "not in tests/corpus" in problems[1]


# --- other declaration rules


@pytest.mark.parametrize("bad_id", ["PDF.merge", "pdf..merge", "pdf.merge_", "1pdf", "", "pdf/x"])
def test_bad_id_is_rejected(bad_id):
    assert "id" in rejected(id=bad_id)


def test_good_ids_are_accepted():
    for good in ["convert", "pdf.to-pdfa", "cert.create", "ocr.pdf"]:
        assert declare(id=good).id == good


def test_unknown_group_and_category_are_rejected():
    assert "unknown group 'gpu'" in rejected(group="gpu")
    assert "unknown category 'misc'" in rejected(category="misc")


def test_title_and_summary_are_required():
    assert "title" in rejected(title="")
    assert "summary" in rejected(summary=" ")


def test_options_must_extend_tool_options():
    class Opts(BaseModel):
        level: int = Field(1, title="Level", ge=0, le=3)

    assert "ToolOptions" in rejected(options=Opts)


@pytest.mark.parametrize(
    "annotation,field,reason",
    [
        (list[int], Field([], title="Pages"), "unsupported"),
        (str, Field("", title="Name"), "max_length"),
        (int, Field(1, title="Level"), "minimum and maximum"),
        (dict, Field({}, title="Map"), "unsupported"),
    ],
)
def test_options_outside_the_ui_subset_are_rejected(annotation, field, reason):
    Opts = type("Opts", (ToolOptions,), {"__annotations__": {"value": annotation}, "value": field})
    assert reason in rejected(options=Opts)


def test_two_levels_of_nesting_are_rejected():
    class Inner(ToolOptions):
        x: int = Field(0, title="X", ge=0, le=1)

    class Middle(ToolOptions):
        inner: Inner = Field(Inner(), title="Inner")

    class Opts(ToolOptions):
        middle: Middle = Field(Middle(), title="Middle")

    assert "nesting" in rejected(options=Opts)


def test_nested_field_without_title_is_rejected():
    class Inner(ToolOptions):
        x: int = Field(0, ge=0, le=1)

    class Opts(ToolOptions):
        inner: Inner = Field(Inner(), title="Inner")

    assert "inner.x: needs a title" in rejected(options=Opts)


def test_secret_not_write_only_is_rejected():
    class Opts(ToolOptions):
        pw: SecretStr = Field(title="Password", json_schema_extra={"writeOnly": False})

    assert "writeOnly" in rejected(options=Opts)


def test_secret_with_default_value_is_rejected():
    class Opts(ToolOptions):
        pw: SecretStr = Field(SecretStr("hunter2"), title="Password")

    assert "default" in rejected(options=Opts)


def test_optional_secret_is_allowed():
    class Opts(ToolOptions):
        owner_password: SecretStr | None = Secret(title="Owner password", optional=True)

    prop = declare(options=Opts).options_schema["properties"]["ownerPassword"]
    assert prop["writeOnly"] is True and prop["default"] is None


def test_nested_options_must_extend_tool_options():
    class Inner(BaseModel):
        x: int = Field(0, title="X", ge=0, le=1)

    class Opts(ToolOptions):
        inner: Inner = Field(Inner(), title="Inner")

    assert "ToolOptions" in rejected(options=Opts)


def test_converter_rules():
    edges = [Edge("docx", "pdf")]

    class SecretOpts(ToolOptions):
        pw: SecretStr = Secret(title="Password")

    class RequiredOpts(ToolOptions):
        dpi: int = Field(title="DPI", ge=72, le=600)

    aux = Files(aux={"stamp": Aux(formats=["png"], required=False)})
    assert "not both" in rejected(converts=edges)  # inputs/outputs given as well
    conv = {"inputs": [], "outputs": [], "converts": edges}
    assert "secret" in rejected(**conv, options=SecretOpts)
    assert "auxiliary" in rejected(**conv, files=aux)
    assert "default" in rejected(**conv, options=RequiredOpts)
    assert "unknown format 'docz'" in rejected(
        inputs=[], outputs=[], converts=[Edge("docz", "pdf")]
    )
    assert "itself" in rejected(inputs=[], outputs=[], converts=[Edge("pdf", "pdf")])
    assert "twice" in rejected(inputs=[], outputs=[], converts=edges * 2)
    assert "cost" in rejected(inputs=[], outputs=[], converts=[Edge("docx", "pdf", cost=-1)])


def test_target_option_names_a_literal_of_outputs():
    class Opts(ToolOptions):
        format: Literal["png", "jpg"] = Field("png", title="Format")
        other: Literal["png", "gif"] = Field("png", title="Other")
        dpi: int = Field(150, title="DPI", ge=72, le=600)

    conv = {"inputs": [], "outputs": [], "options": Opts}
    edges = [Edge("pdf", "png"), Edge("pdf", "jpg")]
    t = declare(**conv, converts=edges, target_option="format")
    assert t.target_option == "format" and t.targets(["pdf"]) == {"png", "jpg"}
    assert "only converters" in rejected(options=Opts, target_option="format")
    assert "not an option" in rejected(**conv, converts=edges, target_option="size")
    assert "Literal of output" in rejected(**conv, converts=edges, target_option="dpi")
    assert "Literal of output" in rejected(**conv, converts=edges, target_option="other")


@pytest.mark.parametrize("slot", ["files", "options", "inputs", "target", "Certificate", "my slot"])
def test_reserved_or_bad_aux_slot_names_are_rejected(slot):
    message = rejected(files=Files(aux={slot: Aux(formats=["png"], title="Stamp")}))
    assert f"aux slot {slot!r} must be lowercase snake_case" in message


def test_aux_slot_with_unknown_format_is_rejected():
    assert "unknown format 'pem'" in rejected(files=Files(aux={"key": Aux(formats=["pem"])}))


@pytest.mark.parametrize(
    "files,inputs,reason",
    [
        (Files(min=2, max=1), ["pdf"], "max"),
        (Files(min=-1, max=1), ["pdf"], "min"),
        (Files(min=0, max=0), ["pdf"], "no input files"),
        (Files(min=1, max=1), [], "inputs"),
        (Files(min=0, max=0, each=True), [], "each"),
    ],
)
def test_bad_file_cardinality_is_rejected(files, inputs, reason):
    assert reason in rejected(files=files, inputs=inputs)


def test_tool_needs_an_output_format():
    assert "outputs" in rejected(outputs=[])


def test_bad_limits_are_rejected():
    assert "timeout_s" in rejected(limits=Limits(timeout_s=0))
    assert "memory_mb" in rejected(limits=Limits(memory_mb=-5))


def test_warning_codes_must_be_upper_snake():
    assert "TargetNotReached" in rejected(warnings=["TargetNotReached"])
    assert declare(warnings=["TARGET_NOT_REACHED"]).warnings == ("TARGET_NOT_REACHED",)


def test_declared_error_codes_are_kept():
    t = declare(errors=["INVALID_PDF", "ENCRYPTED_PDF"])
    assert t.errors == ("INVALID_PDF", "ENCRYPTED_PDF")
    assert declare().errors == ()


def test_error_codes_must_be_upper_snake():
    assert "error code 'BadPdf' must be UPPER_SNAKE" in rejected(errors=["BadPdf"])


@pytest.mark.parametrize("code", ["WORKER_LOST", "SECRET_UNAVAILABLE", "ENGINE_TIMEOUT"])
def test_runner_error_codes_cannot_be_declared(code):
    assert f"error code {code!r} is set only by the runner" in rejected(errors=[code])


def test_common_error_codes_need_no_declaration():
    assert "error code 'ENGINE_FAILED' is common" in rejected(errors=["ENGINE_FAILED"])
    assert COMMON_ERRORS.isdisjoint(RUNNER_ERRORS)


def test_secret_inside_nested_options_is_rejected():
    class Inner(ToolOptions):
        pw: SecretStr = Secret(title="Password")

    class Opts(ToolOptions):
        inner: Inner = Field(title="Inner")

    assert "inner.pw: secrets are only allowed at the top level" in rejected(options=Opts)


def test_example_options_must_validate():
    class Opts(ToolOptions):
        level: int = Field(1, title="Level", ge=0, le=3)

    assert "example 0" in rejected(
        options=Opts, examples=[Example(inputs=["pdf/text_1p.pdf"], options={"level": 9})]
    )


def test_example_input_count_must_fit_files():
    ex = Example(inputs=["pdf/text_1p.pdf", "pdf/text_10p.pdf"])
    assert "example 0" in rejected(files=Files(min=1, max=1), examples=[ex])


def test_converter_example_needs_a_reachable_target():
    conv = {"inputs": [], "outputs": [], "converts": [Edge("docx", "pdf")]}
    assert "target" in rejected(**conv, examples=[Example(inputs=["office/report.docx"])])
    assert "target" in rejected(
        **conv, examples=[Example(inputs=["office/report.docx"], target="epub")]
    )
    ok = declare(**conv, examples=[Example(inputs=["office/report.docx"], target="pdf")])
    assert ok.examples[0].target == "pdf"


def test_all_problems_are_reported_together():
    message = rejected(group="gpu", inputs=["nope"])
    assert "unknown group" in message and "unknown format" in message


# --- review fixes (task 2 review L3-L6, nits)


def test_one_tool_bound_to_two_names_is_not_a_duplicate():
    assert list(discover("alias_tools")) == ["alias.one"]


def test_optional_secret_must_be_annotated_optional():
    class Opts(ToolOptions):
        pw: SecretStr = Secret(title="Password", optional=True)

    assert "pw: an optional secret must be annotated SecretStr | None" in rejected(options=Opts)


def test_secret_max_length_follows_the_string_rule():
    class Default(ToolOptions):
        pw: SecretStr = Secret(title="Password")

    assert declare(options=Default).options_schema["properties"]["pw"]["maxLength"] == 1000

    class TooLong(ToolOptions):
        pw: SecretStr = Secret(title="Password", max_length=1001)

    assert "pw: strings need max_length <= 1000" in rejected(options=TooLong)


def test_enum_option_gets_a_clear_error():
    import enum

    class Mode(enum.Enum):
        A = "A"
        B = "B"

    class Opts(ToolOptions):
        mode: Mode = Field(Mode.A, title="Mode")

    assert "mode: use Literal[...] for choices, not Enum" in rejected(options=Opts)


def test_single_value_literal_gets_a_clear_error():
    from typing import Literal

    class Opts(ToolOptions):
        mode: Literal["ONLY"] = Field("ONLY", title="Mode")

    assert "mode: a Literal with one value is a constant, not an option" in rejected(options=Opts)


def test_choice_titles_reach_the_schema():
    from typing import Literal

    class Opts(ToolOptions):
        mode: Literal["each", "all"] | None = Field(
            None,
            title="Mode",
            json_schema_extra={"x-enum-titles": ["One per file", "One for all"]},
        )

    prop = declare(options=Opts).options_schema["properties"]["mode"]
    assert prop["x-enum-titles"] == ["One per file", "One for all"]


@pytest.mark.parametrize(
    "extra",
    [
        {"x-enum-titles": ["Only one"]},
        {"x-enum-titles": ["A", "B", "C"]},
        {"x-enum-titles": ["A", ""]},
        {"x-enum-descriptions": ["A"]},
        {"x-enum-titles": "A, B"},
    ],
)
def test_choice_titles_must_match_the_choices(extra):
    from typing import Literal

    class Opts(ToolOptions):
        mode: Literal["a", "b"] = Field("a", title="Mode", json_schema_extra=extra)

    assert "mode: x-enum-" in rejected(options=Opts)


def test_target_on_a_non_converter_example_is_rejected():
    ex = Example(inputs=["pdf/text_1p.pdf"], target="pdf")
    assert "example 0: only converters take a target" in rejected(examples=[ex])


def test_converter_example_target_must_be_an_edge_from_the_input_format():
    t = declare(
        inputs=[],
        outputs=[],
        category="convert",
        converts=[Edge("pdf", "png"), Edge("docx", "pdf")],
        examples=[
            Example(inputs=["pdf/text_1p.pdf"], target="pdf"),  # pdf->pdf is not an edge
            Example(inputs=["pdf/text_1p.pdf"], target="png"),
            Example(inputs=["office/report.docx"], target="pdf"),
        ],
    )
    problems = example_problems(t)
    assert problems == ["example 0: no edge pdf->pdf for target 'pdf'"]


# --- search keywords


def test_keywords_are_kept_and_default_to_none():
    assert declare().keywords == ()
    assert declare(keywords=["combine", "e-book"]).keywords == ("combine", "e-book")


@pytest.mark.parametrize(
    "keywords,reason",
    [
        (["Combine"], "lowercase"),
        (["two words"], "lowercase"),
        ([""], "lowercase"),
        (["a" * 31], "30"),
        ([f"k{i}" for i in range(21)], "20"),
        (["join", "join"], "twice"),
        ("join", "list"),
    ],
)
def test_bad_keywords_are_rejected(keywords, reason):
    assert reason in rejected(keywords=keywords)


# --- list options (pdf.organize's page plan)


class _Step(ToolOptions):
    page: int = Field(title="Page", ge=1, le=10)


def _list_opts(field):
    return type("Opts", (ToolOptions,), {"__annotations__": {"steps": list[_Step]}, "steps": field})


def test_a_bounded_list_of_models_with_a_widget_is_allowed():
    Opts = _list_opts(Field(title="Steps", max_length=5, json_schema_extra={"x-widget": "grid"}))
    prop = declare(options=Opts).options_schema["properties"]["steps"]
    assert (prop["type"], prop["maxItems"], prop["x-widget"]) == ("array", 5, "grid")
    assert Opts.model_validate({"steps": [{"page": 2}, {"page": 2}]}).steps[1].page == 2


@pytest.mark.parametrize(
    "field,reason",
    [
        (Field(title="Steps", json_schema_extra={"x-widget": "grid"}), "max_length <= 2000"),
        (
            Field(title="Steps", max_length=2001, json_schema_extra={"x-widget": "grid"}),
            "max_length <= 2000",
        ),
        (Field(title="Steps", max_length=5), "x-widget"),
    ],
)
def test_lists_need_a_bound_and_a_widget(field, reason):
    assert reason in rejected(options=_list_opts(field))


def test_list_items_follow_the_option_rules():
    class Item(ToolOptions):
        page: int = Field(title="Page", ge=1)  # no maximum

    class Opts(ToolOptions):
        steps: list[Item] = Field(title="Steps", max_length=5, json_schema_extra={"x-widget": "g"})

    assert "options.steps[].page: numbers need a minimum and maximum" in rejected(options=Opts)


def test_list_inside_nested_options_is_rejected():
    class Inner(ToolOptions):
        steps: list[_Step] = Field(title="Steps", max_length=5, json_schema_extra={"x-widget": "g"})

    class Opts(ToolOptions):
        inner: Inner = Field(title="Inner")

    assert "lists are only allowed at the top level" in rejected(options=Opts)
