import fake_engine  # module-level engine import: the API image would fail to load this

from filex.registry import tool


@tool(
    id="bad.engine-import",
    title="Engine import",
    summary="Imports an engine at module level.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
)
def run(ctx, src, opts):
    return [fake_engine]
