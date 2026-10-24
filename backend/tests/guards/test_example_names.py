"""The owner's real name must not come back as an example, fixture or demo name (docs/design/example-names.md)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "rename_example_names.py"


def _load():
    spec = importlib.util.spec_from_file_location("rename_example_names", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_disallowed_occurrences_remain() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(["python3", str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mapping_is_per_layer_and_case_aware() -> None:
    m = _load()
    assert m.rewrite("Shashank Rao <shashank@example.com> SHASHANK", "backend/tests/x.py") == (
        "Jordan Whitfield <jordan@example.com> JORDAN"
    )
    assert m.rewrite("Shashank Rao, shashank-rao, Shashank K", "e2e/tests/x.ts") == (
        "Ananya Menon, ananya-menon, Ananya M"
    )
    assert m.rewrite("U_DEMO_SHASHANK ava_for_shashank_bot", "backend/src/x.py") == "U_DEMO_JORDAN ava_for_jordan_bot"


def test_rewrite_is_idempotent() -> None:
    m = _load()
    once = m.rewrite("Shashank Rao and shashank@example.com", "docs/a.md")
    assert m.rewrite(once, "docs/a.md") == once


def test_allowlist_keeps_the_real_owner_notes() -> None:
    m = _load()
    whole, lines = m.load_allowlist()
    assert "CLAUDE.md" in whole
    assert m.process("CLAUDE.md", whole, lines, write=False) == []


def test_real_companies_map_to_fictional_ones_keeping_case() -> None:
    m = _load()
    path = "backend/tests/x.py"
    assert (
        m.rewrite("Pebble Road, PebbleRoad, pebble-road, PEBBLEROAD", path)
        == "Larkfield, Larkfield, larkfield, LARKFIELD"
    )
    assert (
        m.rewrite("noreply@account.bluehost.com Bluehost BLUEHOST", path)
        == "noreply@account.fernhost.example Fernhost FERNHOST"
    )
    assert (
        m.rewrite("Kotak Mahindra Bank <a@kotak.com> at SWIGGY", path) == "Marlin Bank <a@marlin.example> at TEAHOUSE"
    )
    once = m.rewrite("Pebble Road and Bluehost", path)
    assert m.rewrite(once, path) == once


def test_stripe_is_only_replaced_where_it_is_a_fixture_employer() -> None:
    m = _load()
    assert m.rewrite("interviewing at Stripe", "backend/evals/scenarios/privacy.yaml") == "interviewing at Quillstone"
    assert m.rewrite("billing runs on Stripe", "docs/decisions/0009-many-owners-one-ava.md") == "billing runs on Stripe"


def test_the_guard_fails_on_a_real_employer_name_outside_the_allowlist() -> None:
    m = _load()
    whole, lines = m.load_allowlist()
    assert m.has_old("I work at PebbleRoad", "e2e/tests/x.spec.ts")
    assert "docs/overnight-log.md" in whole  # the owner's real email lives only in the allowlisted logs
    assert m.process("deploy/README.md", whole, lines, write=False) == []  # a real hosting provider named on purpose
