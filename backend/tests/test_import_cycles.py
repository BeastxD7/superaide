# ci-module: repo
"""The import cycles ADR 0015 removes stay removed (ratchet).

Uses the graph scripts/select_tests.py and scripts/layout_report.py use (lazy and TYPE_CHECKING imports count),
so a lazy import that quietly closes a cycle again fails here, not months later as a slow CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def graph():
    return _load("layout_report")._load_selector().build_graph(ROOT)


# Importer -> what it must not import any more (dotted file ids, as in docs/design/module-graph.md).
FORBIDDEN: dict[str, set[str]] = {
    # technique 1: model wiring is not the factory, so nothing but the composition roots needs the pipeline for it
    "factory": set(),
    # technique 2: policy knows action kinds through registries, never the modules that carry them out
    "policy.actions": {"skills.calendar", "skills.gmail", "skills.receipts", "asklink.service", "voice.gateway"},
    "policy.kinds": {"policy.actions"},
    "policy.footprint": {"policy.actions"},
    "evals.checks": {"evals.harness"},
    # technique 3: notifications find the channel adapters through channels.base registries
    "proactive.notify": {"channels.telegram", "channels.slack"},
    "channels.telegram": {"triage.buttons"},
    "brain.keyhealth": {"proactive.notify"},
    # technique 4: reactions to "a source was read / removed" are event subscribers, not calls from the skills
    "skills.registry": {"knowledge.connector_facts"},
    "skills.calendar": {"knowledge.connector_facts"},
    "skills.gmail_inbox": {"knowledge.connector_facts", "triage.digest", "triage.routes"},
    "triage.digest": {"skills.gmail_inbox"},
    "triage.routes": {"skills.gmail_inbox"},
}


def test_removed_edges_stay_removed(graph):
    back = [
        f"{src} -> {dst}" for src, banned in FORBIDDEN.items() for dst in sorted(banned & graph.imports.get(src, set()))
    ]
    assert not back, "imports ADR 0015 removed are back (use the registry / event bus instead): " + "; ".join(back)


def test_only_the_composition_roots_import_the_factory(graph):
    importers = {f for f, targets in graph.imports.items() if "factory" in targets}
    assert importers <= {"runtime", "cli"}, importers


def test_routers_share_deps_not_the_app_router_module(graph):
    """technique 5: ~30 routers imported app.routes only for owner_person_id; they take it from app.deps."""
    importers = {f for f, targets in graph.imports.items() if "app.routes" in targets}
    assert importers <= {"main"}, importers


def test_the_agent_core_is_out_of_every_cycle(graph):
    cycles = _load("layout_report").sccs(sorted(graph.imports), graph.imports)
    in_cycle = {f for c in cycles for f in c}
    core = {"pipeline", "brain.loop", "brain.capabilities", "factory", "policy.actions", "policy.kinds", "app.routes"}
    assert not core & in_cycle, sorted(core & in_cycle)


def test_policy_does_not_depend_on_what_it_gates(graph):
    leaked = sorted(
        f"{src} -> {dst}"
        for src, targets in graph.imports.items()
        if src.startswith("policy.")
        for dst in targets
        if dst.split(".")[0] in {"skills", "asklink", "voice", "app", "brain", "triage", "jobs"}
    )
    assert not leaked, leaked


def test_the_biggest_import_cycle_only_shrinks(graph):
    lr = _load("layout_report")
    cycles = lr.sccs(sorted(graph.imports), graph.imports)
    biggest = max((len(c) for c in cycles), default=0)
    assert biggest <= 14, f"the largest file-level import cycle grew to {biggest} files (ADR 0015 phase 2)"
