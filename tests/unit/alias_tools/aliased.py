from filex.registry import tool


@tool(
    id="alias.one",
    title="One",
    summary="One tool bound to two names. Test fixture.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
)
def run(ctx, src, opts):
    return []


main = run  # the same Tool object under a second name is not a duplicate
