"""The sleep job's change log and undo (#415): a record per kind, exact undo, append-only, never a delete."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.memory.consolidation import changes, sleep
from superaide.memory.models import ConsolidationChange, Episode, MemoryItem
from superaide.platform.db.session import session_scope
from tests.memory.test_sleep import FILLER, NOW, SET, Judge, notes, pair_world, world


def rows(s) -> list[ConsolidationChange]:
    return list(s.scalars(select(ConsolidationChange).order_by(ConsolidationChange.created_at, ConsolidationChange.id)))


def fields(s) -> dict[uuid.UUID, dict]:
    """Every column of every note, to compare the world before a run and after its undo."""
    s.flush()
    return {
        m.id: {c.key: getattr(m, c.key) for c in MemoryItem.__table__.columns} for m in s.scalars(select(MemoryItem))
    }


def test_an_exact_duplicate_writes_a_record_with_why_source_before_and_after():
    with session_scope() as s:
        o, _ = world(s)
        doc = notes(s, o, ["Jordan is the CEO of Orchard Labs."], source="document")[0]
        said = notes(s, o, ["jordan is the CEO of Orchard Labs"], source="chat")[0]
        notes(s, o, [f"Fact number {i} about Jordan." for i in range(6)])
        run = sleep.run_for_owner(s, o, NOW)
        (c,) = rows(s)
        assert (c.kind, c.decided_by, c.source, c.run_id) == ("duplicate", "code", "document", run.id)
        assert c.memory_id == doc.id and c.kept_id == said.id and "capitals" in c.reason
        assert c.before["retired"] == {"valid_to": None, "superseded_by": None}
        assert c.after["retired"]["valid_to"] == NOW.isoformat() and c.after["retired"]["superseded_by"] is None
        assert c.before["keeper"]["source_event_ids"] == [] and c.owner_person_id == o.id


def test_a_near_duplicate_writes_a_record_naming_the_model():
    with session_scope() as s:
        o, a, b = pair_world(s, "My office is in Pune near the river.", "My office is located in Pune by the river.")
        sleep.run_for_owner(s, o, NOW, Judge(["same"]), SET)
        (c,) = rows(s)
        assert (c.kind, c.decided_by) == ("near_duplicate", "model") and c.memory_id in {a.id, b.id}


def test_a_contradiction_writes_a_record_pointing_at_the_successor():
    with session_scope() as s:
        o, a, b = pair_world(s, "My desk is in Pune near the river.", "My desk is in Mumbai near the river.")
        sleep.run_for_owner(s, o, NOW, Judge(["newer_replaces"]), SET)
        (c,) = rows(s)
        assert (c.kind, c.decided_by, c.memory_id, c.kept_id) == ("contradiction", "model", a.id, b.id)
        assert c.after["retired"]["superseded_by"] == str(b.id)


def test_a_run_that_changes_nothing_writes_no_record():
    with session_scope() as s:
        o, _ = world(s)
        notes(s, o, ["One fact.", "Another, different fact."])
        sleep.run_for_owner(s, o, NOW)
        assert rows(s) == []


def test_undo_puts_the_note_back_appends_an_undo_row_and_only_works_once():
    with session_scope() as s:
        o, a, b = pair_world(s, "My office is in Pune near the river.", "My office is in Mumbai near the river.")
        sleep.run_for_owner(s, o, NOW, Judge(["newer_replaces"]), SET)
        (c,) = rows(s)
        original = {k: getattr(c, k) for k in ("kind", "before", "after", "reason", "memory_id")}
        undo = changes.undo(s, o.id, c.id)
        assert a.valid_to is None and a.superseded_by is None
        assert undo.kind == "undo" and undo.undoes_id == c.id and undo.decided_by == "owner"
        assert {k: getattr(c, k) for k in original} == original  # the original record is never edited
        with pytest.raises(changes.ChangeError) as e:
            changes.undo(s, o.id, c.id)
        assert e.value.status == 409
        with pytest.raises(changes.ChangeError) as e:
            changes.undo(s, o.id, undo.id)  # an undo row is not itself undoable
        assert e.value.status == 404


def test_undo_is_refused_when_the_owner_forgot_the_note_since_and_for_someone_elses_change():
    with session_scope() as s:
        o, a, b = pair_world(s, "My office is in Pune near the river.", "My office is in Mumbai near the river.")
        sleep.run_for_owner(s, o, NOW, Judge(["newer_replaces"]), SET)
        (c,) = rows(s)
        with pytest.raises(changes.ChangeError) as e:
            changes.undo(s, uuid.uuid4(), c.id)  # not the owner
        assert e.value.status == 404
        a.valid_to = NOW + timedelta(days=1)  # the owner forgot it afterwards
        with pytest.raises(changes.ChangeError) as e:
            changes.undo(s, o.id, c.id)
        assert e.value.status == 409 and a.valid_to == NOW + timedelta(days=1)


def test_a_kept_note_that_changed_since_keeps_its_newer_details_while_the_retired_note_returns():
    with session_scope() as s:
        o, _ = world(s)
        doc = notes(s, o, ["Jordan is the CEO of Orchard Labs."], source="document")[0]
        said = notes(s, o, ["jordan is the CEO of Orchard Labs"], source="chat")[0]
        notes(s, o, [f"Fact number {i} about Jordan." for i in range(6)])
        sleep.run_for_owner(s, o, NOW)
        (c,) = rows(s)
        said.confidence = 0.123  # confirmed again by the owner after the job ran
        undo = changes.undo(s, o.id, c.id)
        assert doc.valid_to is None and said.confidence == 0.123 and "newer details" in undo.reason


def test_restoring_a_run_goes_through_the_log_and_records_each_undo():
    with session_scope() as s:
        o, _ = world(s)
        for i in range(4):
            notes(s, o, [f"Fact {i} about the owner.", f"fact {i} about the owner"])
        run = sleep.run_for_owner(s, o, NOW)
        assert sleep.restore(s, run.id) == 2
        assert [r.kind for r in rows(s)].count("undo") == 2 and len(sleep._owner_notes(s, o)) == 8


def build(s, rng: random.Random):
    """A random owner notebook: exact copies (some from files), near copies, replacements and fillers."""
    o, _ = world(s)
    n = 0
    for _ in range(rng.randint(2, 6)):
        text = f"Fact {n} about {rng.choice(['Pune', 'Lisbon', 'Orchard', 'Kochi'])} and the river."
        n += 1
        for copy in range(rng.randint(1, 3)):
            notes(s, o, [text.upper() if copy % 2 else text], source=rng.choice(["chat", "document", "link"]))
    notes(s, o, FILLER[: rng.randint(0, len(FILLER))])
    for m in s.scalars(select(MemoryItem)):
        m.confidence = round(rng.random(), 2)
    return o


@pytest.mark.parametrize("seed", range(12))
def test_property_undoing_every_change_restores_the_exact_prior_state(seed):
    rng = random.Random(seed)
    with session_scope() as s:
        o = build(s, rng)
        before = fields(s)
        judge = Judge([rng.choice(["same", "newer_replaces", "different"]) for _ in range(30)])
        sleep.run_for_owner(s, o, NOW, judge, SET)
        made = rows(s)
        assert made  # the seeded notebook always has something to tidy
        assert len(made) == len({c.memory_id for c in made})
        for c in sorted(made, key=lambda c: (c.created_at, c.id), reverse=True):  # newest first
            changes.undo(s, o.id, c.id)
        assert fields(s) == before
        assert len(rows(s)) == 2 * len(made) and {r.kind for r in rows(s)} <= {*changes.KINDS, "undo"}


@pytest.mark.parametrize("seed", range(6))
def test_property_undoing_in_any_order_never_loses_a_note(seed):
    rng = random.Random(100 + seed)
    with session_scope() as s:
        o = build(s, rng)
        live_before = {m.id for m in s.scalars(select(MemoryItem))}
        sleep.run_for_owner(s, o, NOW, Judge(["same"] * 30), SET)
        made = rows(s)
        rng.shuffle(made)
        for c in made:
            changes.undo(s, o.id, c.id)
        assert {m.id for m in s.scalars(select(MemoryItem))} == live_before
        assert all(m.valid_to is None for m in s.scalars(select(MemoryItem)))


def test_no_sleep_job_path_deletes_a_row(monkeypatch):
    def boom(self, instance):
        raise AssertionError(f"the sleep job deleted a {type(instance).__name__}")

    with session_scope() as s:
        o, _ = world(s)
        for i in range(4):
            notes(s, o, [f"Fact {i} about the owner.", f"fact {i} about the owner"])
        n = s.scalar(select(func.count()).select_from(MemoryItem))
        monkeypatch.setattr(Session, "delete", boom)
        run = sleep.run_for_owner(s, o, NOW, Judge(["same"] * 20), SET)
        sleep.restore(s, run.id)
        sleep.run_for_owner(s, o, NOW + timedelta(days=1), Judge(["same"] * 20), SET)
        assert s.scalar(select(func.count()).select_from(MemoryItem)) == n  # retired, never removed
        assert s.scalar(select(func.count()).select_from(Episode)) is not None


def test_the_job_and_the_log_source_files_contain_no_delete():
    root = Path(sleep.__file__).parent
    for name in ("sleep.py", "merge.py", "changes.py"):
        text = (root / name).read_text()
        assert ".delete(" not in text and "delete(" not in text.replace("undelete", "") and " DELETE " not in text, name
        assert "update(ConsolidationChange" not in text, name  # the log is append-only
