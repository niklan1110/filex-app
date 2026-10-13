from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import Example, Files, Limits, StepContext, ToolOptions, tool


class Options(ToolOptions):
    mode: Literal["COPY", "NOTHING"] = Field("COPY", title="Mode")
    repeat_count: int = Field(1, title="Repeat count", ge=1, le=3)


@tool(
    id="sample.echo",
    title="Echo",
    summary="Copies the input. Test fixture.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=2, each=True),
    options=Options,
    engines=["imaginary-engine"],
    limits=Limits(timeout_s=10, memory_mb=64),
    examples=[Example(inputs=["pdf/text_1p.pdf"], options={"mode": "COPY"})],
    warnings=["NOTHING_DONE"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import fake_engine  # engine imports stay inside run()

    out = ctx.out_dir / "0.pdf"
    out.write_bytes(src.read_bytes() if fake_engine.ok() else b"")
    return [out]
