"""Split the suite into N shards by test module, for CI (`pytest --shard 1/2`).

Modules are placed, biggest first, on the shard with the fewest tests so far. The result depends only
on the collected test ids, so every pytest-xdist worker (which collects on its own) and every run
agree, and each test lands on exactly one shard.
"""

from __future__ import annotations

from collections import Counter


def parse(spec: str) -> tuple[int, int]:
    index, _, total = spec.partition("/")
    i, n = int(index), int(total)
    if not 1 <= i <= n:
        raise ValueError(f"--shard must look like 1/2 (index from 1 up to the total), got {spec!r}")
    return i, n


def module_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def assign(nodeids: list[str], total: int) -> dict[str, int]:
    """Module -> shard number (1-based)."""
    sizes = Counter(module_of(n) for n in nodeids)
    load = [0] * total
    out: dict[str, int] = {}
    for module, size in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])):
        shard = min(range(total), key=lambda s: (load[s], s))
        out[module] = shard + 1
        load[shard] += size
    return out
