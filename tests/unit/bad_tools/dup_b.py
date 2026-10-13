from filex.registry import tool


@tool(
    id="bad.dup",
    title="Dup",
    summary="Same id twice.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
)
def run(ctx, src, opts):
    return []
