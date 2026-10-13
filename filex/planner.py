"""The conversion planner (ADR-0005): cheapest route over the converters' declared edges.

Formats are nodes and every ``Edge`` of a converter tool is a directed edge. A hop costs 1, +4
when lossy, plus the edge's own ``cost``. Ties break on fewer steps, then the tools' ids, then the
intermediate formats' ids, so a plan never depends on dict or heap order. At most ``MAX_STEPS``.

A job's files may have different formats (``jpg`` and ``png`` to ``pdf``): the first hop must be
one tool with an edge from every one of them to the same format; after it every file has that
format. The route then is the same for every file, so the job's steps are one route.

Pure: no Redis, no I/O. ``online`` is the set of tool ids in live heartbeats (``None`` = assume
every tool is online, which is how ``NO_ROUTE`` finds the profiles that would help).
"""

from __future__ import annotations

import heapq
from collections.abc import Collection, Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from filex.registry import Edge, Tool

MAX_STEPS = 4
LOSSY_COST = 4


@dataclass(frozen=True)
class Hop:
    tool: str
    group: str
    src: tuple[str, ...]  # the formats going in: several only on the first hop of mixed files
    dst: str
    lossy: bool


@dataclass(frozen=True)
class Route:
    hops: tuple[Hop, ...]
    cost: int

    @property
    def groups(self) -> list[str]:
        return sorted({h.group for h in self.hops})


def hop_cost(edges: Iterable[Edge]) -> tuple[int, bool]:
    """Cost and lossiness of one hop that uses ``edges`` (one per input format)."""
    edges = list(edges)
    lossy = any(e.lossy for e in edges)
    return 1 + (LOSSY_COST if lossy else 0) + max(e.cost for e in edges), lossy


def _usable(tools: Iterable[Tool], online: AbstractSet[str] | None, files: int) -> list[Tool]:
    return [
        t
        for t in sorted(tools, key=lambda t: t.id)
        if t.converts and (online is None or t.id in online) and t.files.min <= files <= t.files.max
    ]


def routes_from(
    tools: Iterable[Tool],
    sources: Collection[str],
    online: AbstractSet[str] | None = None,
    files: int = 1,
) -> dict[str, Route]:
    """The best route from ``sources`` (the job's input formats) to every reachable format."""
    usable = _usable(tools, online, files)
    by_src: dict[str, list[tuple[Tool, Mapping[str, Edge]]]] = {}
    for t in usable:
        out: dict[str, dict[str, Edge]] = {}
        for e in t.converts:
            out.setdefault(e.src, {})[e.dst] = e
        for src, edges in out.items():
            by_src.setdefault(src, []).append((t, edges))
    start = frozenset(sources)
    if not start:
        return {}
    # Heap items: (cost, steps, tool ids, dst ids, current formats, hops). The key is
    # consistent under extension, so the first time a format is popped its route is the best.
    heap: list = [(0, 0, (), (), tuple(sorted(start)), ())]
    seen: set[tuple[tuple[str, ...], int]] = set()
    best: dict[str, Route] = {}
    while heap:
        cost, steps, ids, dsts, here, hops = heapq.heappop(heap)
        if (here, steps) in seen:
            continue
        seen.add((here, steps))
        if steps and here[0] not in best:
            best[here[0]] = Route(hops, cost)
        if steps == MAX_STEPS:
            continue
        for t, dst, edges in _next_hops(by_src, here):
            if dst in start:
                continue  # never back to an input format (a pdf -> azw3 -> pdf round trip)
            c, lossy = hop_cost(edges)
            hop = Hop(t.id, t.group, here, dst, lossy)
            heapq.heappush(
                heap,
                (cost + c, steps + 1, (*ids, t.id), (*dsts, dst), (dst,), (*hops, hop)),
            )
    return best


def _next_hops(by_src, here: tuple[str, ...]):
    """(tool, dst, edges) for every tool with an edge from each format in ``here`` to ``dst``."""
    first, rest = here[0], here[1:]
    for t, edges in by_src.get(first, []):
        for dst, e in sorted(edges.items()):
            others = []
            for src in rest:
                other = next((x for tt, x in by_src.get(src, []) if tt is t), {}).get(dst)
                if other is None:
                    break
                others.append(other)
            else:
                yield t, dst, [e, *others]


def route(
    tools: Iterable[Tool],
    sources: Collection[str],
    target: str,
    online: AbstractSet[str] | None = None,
    files: int = 1,
) -> Route | None:
    """The cheapest route from ``sources`` to ``target``, or None."""
    return routes_from(tools, sources, online, files).get(target)
