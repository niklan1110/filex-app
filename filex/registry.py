"""Tool declarations and the registry (ADR-0003, architecture section 6).

A tool is a module under ``filex.tools`` with ``@tool(...)`` on its ``run`` function. The
declaration is checked when the module is imported, so a bad tool stops the API and workers
from starting instead of failing on the first job. ``discover()`` builds the registry.

Tool modules import only the stdlib, pydantic and ``filex`` at module level; engine imports
live inside ``run()`` (the generic tests check this).
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic.alias_generators import to_camel

from filex.formats import FORMATS

GROUPS = frozenset({"core", "office", "ocr", "ebook", "ml", "ai"})  # ADR-0002, ADR-0010
CATEGORIES = frozenset(
    {"organize", "optimize", "convert", "ebook", "ocr", "edit", "security", "compare", "ai"}
)  # spec section 6
RESERVED_PARTS = frozenset(
    {"files", "options", "inputs", "target"}
)  # multipart names; inputs: ADR-0003 A5

_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)*")
_SLOT = re.compile(r"[a-z][a-z0-9_]*")
_CODE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*")
_KEYWORD = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # one lowercase word, hyphens allowed
MAX_KEYWORDS, MAX_KEYWORD_LEN = 20, 30
_MAX_STRING = 1000
# List options (``list[Model]``, e.g. pdf.organize's page plan) hold at most this many entries;
# 2000 small entries stay under the API's 64 KiB options part.
MAX_LIST_ITEMS = 2000

# Job error codes (spec 7.1). Any step may report a common code without declaring it; other
# codes a tool raises with StepError must be in its ``errors``. Runner codes describe what
# happened around the step, so a tool can never claim them. Anything else becomes ENGINE_FAILED.
COMMON_ERRORS = frozenset(
    {
        "ENGINE_FAILED",
        "ENGINE_MEMORY",
        "INVALID_INPUT",
        "INVALID_OPTIONS",
        "INVALID_OUTPUT",
        "SANDBOX_FAILED",
    }
)
RUNNER_ERRORS = frozenset(
    {
        "ENGINE_TIMEOUT",
        "INPUT_MISSING",
        "OUTPUT_TOO_LARGE",
        "SECRET_UNAVAILABLE",
        "TOOL_UNAVAILABLE",
        "WORKER_LOST",
    }
)


class ToolDeclarationError(Exception):
    """A tool declaration breaks a registry rule. The message lists every problem."""


class ToolOptions(BaseModel):
    """Base for every options model: camelCase on the wire, unknown keys rejected."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, validate_by_alias=True, extra="forbid"
    )


def Secret(
    *,
    title: str,
    min_length: int | None = None,
    max_length: int = _MAX_STRING,
    optional: bool = False,
) -> Any:
    """A ``SecretStr`` option: write-only password field, never stored in the job hash.

    ``optional=True`` needs the annotation ``SecretStr | None``.
    """
    return Field(
        None if optional else ...,
        title=title,
        min_length=min_length,
        max_length=max_length,
        json_schema_extra={"writeOnly": True, "format": "password"},
    )


@dataclass(frozen=True)
class Aux:
    """A named auxiliary file slot next to the main files, e.g. the signing certificate."""

    formats: Sequence[str]
    title: str = ""
    required: bool = True
    sensitive: bool = False  # deleted by the runner as soon as the step ends


@dataclass(frozen=True)
class Files:
    min: int = 1
    max: int = 1
    each: bool = False  # True: the tool runs once per input file
    aux: Mapping[str, Aux] = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    timeout_s: int = 300
    memory_mb: int = 1024
    rlimit_data: bool = False  # Qt WebEngine/Chromium: RLIMIT_DATA instead of RLIMIT_AS


_DEFAULT_FILES = Files()  # frozen, so safe to share as a default argument
_DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Edge:
    """A conversion a converter tool offers; the planner adds +4 for lossy, plus ``cost``."""

    src: str
    dst: str
    lossy: bool = False
    cost: int = 0


@dataclass(frozen=True)
class Example:
    """A smoke test: input paths relative to ``tests/corpus``; ``target`` for converters."""

    inputs: Sequence[str]
    options: Mapping[str, Any] = field(default_factory=dict)
    target: str | None = None


@dataclass(frozen=True)
class FollowUp:
    """A step a static multi-step tool adds after its own (ADR-0005 amendment), e.g.
    ``FollowUp("ocr.pdf", {"mode": "skip"})``. Options by their wire names; the rest default."""

    tool: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """What a tool's ``plan`` hook returns at job creation: the steps that follow the tool's
    own step, and job warnings as ``(code, message)`` pairs (codes from the tool's
    ``warnings``). Fixed when the job is created: no step adds steps at run time."""

    steps: tuple[FollowUp, ...] = ()
    warnings: tuple[tuple[str, str], ...] = ()


# A tool's plan hook: (its validated options, the tool ids online now) -> Plan.
PlanHook = Callable[[Any, AbstractSet[str]], Plan]


MAX_OUTPUTS = 1000  # outputs per step; the runner fails a step with more (OUTPUT_TOO_LARGE)


@dataclass(frozen=True)
class Output:
    """Wrap a result path returned by ``run()`` to give it a display name, e.g.
    ``Output(path, "report-part-001.pdf")``. The name is what the download is saved as (and
    its name inside the result zip). The runner cleans it like an input name and replaces its
    extension with the sniffed format's; "" or a bare ``Path`` means "named after the input"."""

    path: Path
    name: str = ""
    role: ClassVar[str] = "RESULT"


@dataclass(frozen=True)
class Report(Output):
    """Wrap an output path returned by ``run()`` to mark it a report, not the result."""

    role: ClassVar[str] = "REPORT"


class StepError(Exception):
    """Raised by ``run()`` to fail the job with a plain-language message, e.g.
    ``StepError("WRONG_PASSWORD", "The password is wrong.")``. The code must be common or in
    the tool's ``errors``. Other exceptions become ``ENGINE_FAILED`` with a generic message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code, self.message = code, message


class StepContext(Protocol):
    """What ``run()`` gets from the worker runner (task 5 implements it)."""

    out_dir: Path
    aux: Mapping[str, Path]
    target: str | None  # converters: the requested output format
    # Display names of the inputs, aligned with ``src`` (one name when ``files.each``): NFC, no
    # control or bidi characters, at most 200 characters, "" when unknown. For messages and
    # bookmarks only, never for paths.
    input_names: Sequence[str]
    max_pages: int  # FILEX_MAX_PAGES: refuse inputs with more pages
    max_pixels: int  # FILEX_MAX_PIXELS: refuse images or rendered pages with more pixels

    def progress(self, fraction: float, message: str = "") -> None: ...
    def warn(self, code: str, message: str) -> None: ...


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    summary: str
    category: str
    group: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    converts: tuple[Edge, ...]
    files: Files
    options: type[ToolOptions]
    engines: tuple[str, ...]
    limits: Limits
    examples: tuple[Example, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    fn: Callable[..., Any] = field(repr=False)
    module: str
    keywords: tuple[str, ...] = ()  # extra search words for the UI (synonyms of the title)
    target_option: str | None = None  # converters: option naming the target if the job has none
    plan: PlanHook | None = field(default=None, repr=False)  # static multi-step tools

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)

    @property
    def options_schema(self) -> dict[str, Any]:
        return self.options.model_json_schema(by_alias=True)

    def targets(self, formats: Sequence[str]) -> set[str]:
        """Converters: the formats every one of ``formats`` has an edge to (ADR-0005)."""
        found = set(self.outputs)
        for f in set(formats):
            found &= {e.dst for e in self.converts if e.src == f}
        return found


def tool(
    *,
    id: str,
    title: str,
    summary: str,
    category: str,
    group: str,
    inputs: Sequence[str] = (),
    outputs: Sequence[str] = (),
    converts: Sequence[Edge] = (),
    files: Files = _DEFAULT_FILES,
    options: type[ToolOptions] = ToolOptions,
    engines: Sequence[str] = (),
    limits: Limits = _DEFAULT_LIMITS,
    examples: Sequence[Example] = (),
    warnings: Sequence[str] = (),
    errors: Sequence[str] = (),
    keywords: Sequence[str] = (),
    target_option: str | None = None,
    plan: PlanHook | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Declare a tool. ``warnings`` lists the codes ``ctx.warn`` may use and ``errors`` the
    ``StepError`` codes beyond ``COMMON_ERRORS`` (both are contract). ``keywords`` are extra
    search words for the UI: lowercase words, at most 20 of 30 characters each.
    ``target_option`` (converters only) names a ``Literal`` option of output formats that is the
    target when the job gives none, e.g. ``pdf.to-image``'s ``format``. ``plan`` makes a
    static multi-step tool: called at job creation, it returns the steps that follow its own
    (see :class:`Plan`)."""

    def wrap(fn: Callable[..., Any]) -> Tool:
        if converts:
            ins = tuple(sorted({e.src for e in converts}))
            outs = tuple(sorted({e.dst for e in converts}))
        else:
            ins, outs = tuple(inputs), tuple(outputs)
        t = Tool(
            id=id,
            title=title,
            summary=summary,
            category=category,
            group=group,
            inputs=ins,
            outputs=outs,
            converts=tuple(converts),
            files=files,
            options=options,
            engines=tuple(engines),
            limits=limits,
            examples=tuple(examples),
            warnings=tuple(warnings),
            errors=tuple(errors),
            fn=fn,
            module=fn.__module__,
            keywords=(keywords,) if isinstance(keywords, str) else tuple(keywords),
            target_option=target_option,
            plan=plan,
        )
        problems = _problems(t, explicit_io=bool(inputs or outputs))
        if isinstance(keywords, str):
            problems.append("keywords must be a list of words, not one string")
        if problems:
            raise ToolDeclarationError(f"tool {id!r}: " + "; ".join(problems))
        return t

    return wrap


def discover(package: str = "filex.tools") -> dict[str, Tool]:
    """Import every module in ``package`` and return its tools by id, sorted."""
    pkg = importlib.import_module(package)
    found: dict[str, Tool] = {}
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        mod = importlib.import_module(info.name)
        for obj in vars(mod).values():
            if not (isinstance(obj, Tool) and obj.module == mod.__name__):
                continue  # skip tools merely imported from another module
            if found.get(obj.id) is obj:
                continue  # one tool bound to two names, e.g. ``main = run``
            if obj.id in found:
                raise ToolDeclarationError(
                    f"duplicate tool id {obj.id!r} in {found[obj.id].module} and {obj.module}"
                )
            found[obj.id] = obj
    return dict(sorted(found.items()))


# --- declaration rules


def _keyword_problems(keywords: tuple[str, ...]) -> list[str]:
    p: list[str] = []
    if len(keywords) > MAX_KEYWORDS:
        p.append(f"at most {MAX_KEYWORDS} keywords")
    for k in keywords:
        if not (isinstance(k, str) and _KEYWORD.fullmatch(k)):
            p.append(f"keyword {k!r} must be one lowercase word (a-z, 0-9, '-')")
        elif len(k) > MAX_KEYWORD_LEN:
            p.append(f"keyword {k!r} is longer than {MAX_KEYWORD_LEN} characters")
    p += [
        f"keyword {k!r} is listed twice" for k in dict.fromkeys(keywords) if keywords.count(k) > 1
    ]
    return p


def _problems(t: Tool, explicit_io: bool) -> list[str]:
    p: list[str] = []
    if not _ID.fullmatch(t.id):
        p.append("id must be lowercase words joined by '.' or '-', e.g. 'pdf.to-pdfa'")
    if not t.title.strip():
        p.append("title is empty")
    if not t.summary.strip():
        p.append("summary is empty")
    if t.group not in GROUPS:
        p.append(f"unknown group {t.group!r}")
    if t.category not in CATEGORIES:
        p.append(f"unknown category {t.category!r}")
    formats = [*t.inputs, *t.outputs, *(f for a in t.files.aux.values() for f in a.formats)]
    p += [f"unknown format {f!r}" for f in dict.fromkeys(formats) if f not in FORMATS]
    if not t.outputs:
        p.append("declare outputs (or converts)")
    if t.converts:
        p += _converter_problems(t, explicit_io)
    p += _files_problems(t)
    p += [
        f"engine names must be non-empty strings: {e!r}"
        for e in t.engines
        if not (isinstance(e, str) and e.strip())
    ]
    if t.limits.timeout_s <= 0:
        p.append("limits.timeout_s must be > 0")
    if t.limits.memory_mb <= 0:
        p.append("limits.memory_mb must be > 0")
    p += [f"warning code {c!r} must be UPPER_SNAKE" for c in t.warnings if not _CODE.fullmatch(c)]
    for c in t.errors:
        if not _CODE.fullmatch(c):
            p.append(f"error code {c!r} must be UPPER_SNAKE")
        elif c in RUNNER_ERRORS:
            p.append(f"error code {c!r} is set only by the runner")
        elif c in COMMON_ERRORS:
            p.append(f"error code {c!r} is common, do not declare it")
    p += _keyword_problems(t.keywords)
    if t.plan is not None and not callable(t.plan):
        p.append("plan must be a function (options, online tool ids) -> Plan")
    if not (isinstance(t.options, type) and issubclass(t.options, ToolOptions)):
        p.append("options must be a subclass of filex.registry.ToolOptions")
        return p
    p += _options_problems(t.options)
    p += _target_option_problems(t)
    p += _example_problems(t)
    return p


def _target_option_problems(t: Tool) -> list[str]:
    name = t.target_option
    if name is None:
        return []
    if not t.converts:
        return ["only converters declare target_option"]
    f = t.options.model_fields.get(name)
    if f is None:
        return [f"target_option {name!r} is not an option"]
    choices = t.options.model_json_schema(by_alias=True)["properties"][f.alias or name].get("enum")
    if not choices or not set(choices) <= set(t.outputs):
        return [f"target_option {name!r} must be a Literal of output formats"]
    return []


def _converter_problems(t: Tool, explicit_io: bool) -> list[str]:
    p = []
    if explicit_io:
        p.append("declare converts or inputs/outputs, not both")
    p += [f"unknown format {f!r}" for e in t.converts for f in (e.src, e.dst) if f not in FORMATS]
    p += [
        f"edge {e.src}->{e.dst} converts a format to itself" for e in t.converts if e.src == e.dst
    ]
    p += [f"edge {e.src}->{e.dst} has a negative cost" for e in t.converts if e.cost < 0]
    pairs = [(e.src, e.dst) for e in t.converts]
    p += [
        f"edge {s}->{d} declared twice" for s, d in dict.fromkeys(pairs) if pairs.count((s, d)) > 1
    ]
    # Routes are built from defaults (ADR-0005), so nothing a route cannot supply.
    if t.files.aux:
        p.append("converters cannot declare auxiliary files")
    if isinstance(t.options, type) and issubclass(t.options, BaseModel):
        for name, f in t.options.model_fields.items():
            if _is_secret(f.annotation):
                p.append(f"converters cannot declare secret options ({name})")
            elif f.is_required():
                p.append(f"converter option {name} needs a default")
    return p


def _files_problems(t: Tool) -> list[str]:
    f, p = t.files, []
    if f.min < 0:
        p.append("files.min must be >= 0")
    if f.max < f.min:
        p.append("files.max must be >= files.min")
    if f.max == 0 and t.inputs:
        p.append("a tool with no input files cannot declare inputs")
    if f.max > 0 and not t.inputs:
        p.append("a tool that takes files must declare inputs")
    if f.max == 0 and f.each:
        p.append("files.each needs at least one input file")
    for slot, aux in f.aux.items():
        if slot in RESERVED_PARTS or not _SLOT.fullmatch(slot):
            p.append(
                f"aux slot {slot!r} must be lowercase snake_case and not one of "
                f"{sorted(RESERVED_PARTS)}"
            )
        if not aux.title.strip():
            p.append(f"aux slot {slot!r} needs a title")
        if not aux.formats:
            p.append(f"aux slot {slot!r} needs formats")
    return p


def _options_problems(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Options must stay within what the UI form renderer supports (architecture 6, rule 3)."""
    p = []
    props = model.model_json_schema(by_alias=True)["properties"]
    for name, f in model.model_fields.items():
        where = f"options.{prefix}{name}"
        if not f.title:
            p.append(f"{where}: needs a title")
        ann = _strip_optional(f.annotation)
        if typing.get_origin(ann) is list:
            p += _list_problems(where, ann, props[f.alias or name], prefix)
            continue
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            if prefix:
                p.append(f"{where}: nesting deeper than one level is unsupported")
            elif not issubclass(ann, ToolOptions):
                p.append(f"{where}: nested models must extend ToolOptions")
            else:
                p += _options_problems(ann, f"{name}.")
            continue
        prop = props[f.alias or name]
        if _is_secret(f.annotation) and prefix:
            # The API and the step child split secrets out of the top level only.
            p.append(f"{where}: secrets are only allowed at the top level")
            continue
        if _is_secret(f.annotation):
            if prop.get("writeOnly") is not True:
                p.append(f"{where}: secret fields must be writeOnly (use Secret())")
            if not f.is_required() and f.default is not None:
                p.append(f"{where}: a secret cannot have a default value")
            if not f.is_required() and _strip_optional(f.annotation) is f.annotation:
                p.append(f"{where}: an optional secret must be annotated SecretStr | None")
            p += _field_problems(where, prop)  # max_length like any string
            continue
        p += _field_problems(where, prop)
    return p


def _list_problems(where: str, ann: Any, prop: dict[str, Any], prefix: str) -> list[str]:
    """A ``list[Model]`` option: top level only, a flat ``ToolOptions`` item, a bounded length,
    and an ``x-widget`` naming the UI control that edits it (the generic form cannot)."""
    (item,) = typing.get_args(ann) or (None,)
    if not (isinstance(item, type) and issubclass(item, ToolOptions)):
        return [f"{where}: unsupported type (lists must be of a ToolOptions model)"]
    p = []
    if prefix:
        p.append(f"{where}: lists are only allowed at the top level")
    if not 1 <= prop.get("maxItems", MAX_LIST_ITEMS + 1) <= MAX_LIST_ITEMS:
        p.append(f"{where}: lists need max_length <= {MAX_LIST_ITEMS}")
    if not prop.get("x-widget"):
        p.append(f"{where}: lists need an x-widget (json_schema_extra) the UI renders them with")
    return p + _options_problems(item, f"{where.removeprefix('options.')}[].")


def _field_problems(where: str, prop: dict[str, Any]) -> list[str]:
    branches = [b for b in prop.get("anyOf", [prop]) if b.get("type") != "null"]
    if len(branches) != 1:
        return [f"{where}: unsupported type (unions other than X | None)"]
    b = branches[0]
    kind = b.get("type")
    if "$ref" in b or "allOf" in b:
        return [f"{where}: use Literal[...] for choices, not Enum or a model"]
    if "const" in b:
        return [f"{where}: a Literal with one value is a constant, not an option"]
    if "enum" in b:
        return _choice_label_problems(where, prop, len(b["enum"]))
    if kind == "boolean":
        return []
    if kind in ("integer", "number"):
        has_min = "minimum" in b or "exclusiveMinimum" in b
        has_max = "maximum" in b or "exclusiveMaximum" in b
        return [] if has_min and has_max else [f"{where}: numbers need a minimum and maximum"]
    if kind == "string":
        if b.get("maxLength", _MAX_STRING + 1) > _MAX_STRING:
            return [f"{where}: strings need max_length <= {_MAX_STRING}"]
        return []
    return [f"{where}: unsupported type {kind or b}"]


def _choice_label_problems(where: str, prop: dict[str, Any], n: int) -> list[str]:
    """``x-enum-titles`` (what the UI shows instead of each raw value) and
    ``x-enum-descriptions`` (a line under each), set with ``json_schema_extra``: one non-empty
    string per choice, in the Literal's order."""
    p = []
    for key in ("x-enum-titles", "x-enum-descriptions"):
        labels = prop.get(key)
        if labels is None:
            continue
        ok = isinstance(labels, list) and all(isinstance(x, str) and x.strip() for x in labels)
        if not ok or len(labels) != n:
            p.append(f"{where}: {key} needs one non-empty string per choice ({n})")
    return p


def _example_problems(t: Tool) -> list[str]:
    p = []
    for i, ex in enumerate(t.examples):
        if not t.files.min <= len(ex.inputs) <= t.files.max:
            p.append(
                f"example {i}: {len(ex.inputs)} inputs, tool takes {t.files.min}..{t.files.max}"
            )
        try:
            t.options.model_validate(dict(ex.options))
        except ValidationError as e:
            p.append(f"example {i}: invalid options: {e.errors(include_url=False)}")
        if t.converts and ex.target not in t.outputs:
            p.append(f"example {i}: converters need a target among {list(t.outputs)}")
        if not t.converts and ex.target is not None:
            p.append(f"example {i}: only converters take a target")
    return p


def _strip_optional(ann: Any) -> Any:
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return ann


def _is_secret(ann: Any) -> bool:
    return _strip_optional(ann) is SecretStr
