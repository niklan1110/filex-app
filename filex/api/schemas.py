"""Response models of the job API (ADR-0004: the source of the job resources' contract)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorEnvelope(ApiModel):
    error: ErrorBody


class Format(ApiModel):
    id: str
    title: str
    extensions: list[str]
    mime: str


class AuxSlot(ApiModel):
    title: str
    formats: list[str]
    required: bool


class FilesSpec(ApiModel):
    min: int
    max: int
    each: bool
    aux: dict[str, AuxSlot]


class ToolSummary(ApiModel):
    id: str
    title: str
    summary: str
    category: str
    group: str
    inputs: list[str]
    outputs: list[str]
    available: bool
    profile: str  # the compose profile (= worker group) that serves the tool
    keywords: list[str] = Field(
        description="Extra lowercase search words (synonyms of the title) for the tool picker.",
    )


class ConvertEdge(ApiModel):
    src: str
    dst: str
    lossy: bool


class ToolDetail(ToolSummary):
    files: FilesSpec
    options_schema: dict[str, Any]
    converts: list[ConvertEdge] = Field(
        description="Converters: the format pairs the tool converts. Send one dst every input's "
        "format has an edge to as the job's ``target`` part (empty for other tools).",
    )
    target_option: str | None = Field(
        description="Converters: the option (wire name) that is the target when the job sends none.",
    )
    warnings: list[str]
    errors: list[str]  # job error codes the tool declares on top of the common ones


JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


class JobInput(ApiModel):
    name: str
    format: str
    size: int


class JobStep(ApiModel):
    index: int
    tool: str
    group: str
    status: JobStatus
    progress: float
    message: str = ""


class JobWarning(ApiModel):
    code: str
    message: str
    step: int


class ConvertStep(ApiModel):
    index: int
    tool: str
    title: str
    group: str = Field(description="Worker group (= compose profile) that runs the step.")
    src: list[str] = Field(
        description="Formats going in: several only on the first step of mixed files."
    )
    dst: str
    lossy: bool


class ConvertPlan(ApiModel):
    sources: list[str]
    target: str
    cost: int = Field(description="Planner cost: 1 per step, +4 per lossy step, + edge costs.")
    lossy: bool
    steps: list[ConvertStep]
    warnings: list[JobWarning] = Field(
        description="What the job will carry at creation: one LOSSY_STEP per lossy step."
    )


class ConvertTarget(ApiModel):
    format: str
    steps: int
    lossy: bool
    groups: list[str]


class ConvertSource(ApiModel):
    src: str
    targets: list[ConvertTarget] = Field(description="Reachable now, sorted by format id.")


class JobOutput(ApiModel):
    index: int
    name: str
    format: str
    size: int
    role: Literal["RESULT", "REPORT"]


class JobError(ApiModel):
    code: str
    message: str
    step: int | None = None


class Job(ApiModel):
    id: str
    tool: str
    status: JobStatus
    progress: float
    created_at: str | None
    finished_at: str | None
    expires_at: str | None
    inputs: list[JobInput]
    steps: list[JobStep]
    warnings: list[JobWarning]
    outputs: list[JobOutput]
    error: JobError | None


class JobList(ApiModel):
    items: list[Job]
    next_before: int | None = Field(
        description="Pass as ``before`` to get the next, older page; null on the last page."
    )


class ConfigLimits(ApiModel):
    max_upload_mb: int
    max_files: int
    retention_min: int


class Config(ApiModel):
    """Public runtime config for the UI (ADR-0009, A9). Only values that are public anyway."""

    auth_mode: Literal["KEEL", "TOKEN", "NONE"]
    keel_url: str | None
    keel_anon_key: str | None = Field(description="Keel's anon key: public by design.")
    limits: ConfigLimits
    version: str


class StepIsolation(ApiModel):
    seccomp: bool = Field(description="Every step runs under the seccomp filter.")
    landlock: str = Field(
        description="Per-step filesystem confinement: ``abi<N>`` when enforced, ``unavailable`` "
        "when the kernel lacks it, ``off`` when FILEX_LANDLOCK=off (ADR-0007)."
    )


class GroupStatus(ApiModel):
    name: str
    online: bool
    workers: int
    engines: dict[str, str] = Field(description="Engine name to probed version.")
    isolation: StepIsolation | None = Field(
        None, description="The weakest step isolation among the group's live workers."
    )


class Status(ApiModel):
    groups: list[GroupStatus]
    isolation: Literal["container"]
    version: str


class DownloadLink(ApiModel):
    url: str = Field(description="Same-origin ``/api/v1/dl/...`` path; needs no header.")
    expires_at: str
