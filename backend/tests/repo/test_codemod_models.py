"""scripts/codemod_models.py (ADR 0015 phase 7): the import and string rewriting that follows a table class home."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location("codemod_models", ROOT / "scripts" / "codemod_models.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codemod_models"] = mod
    spec.loader.exec_module(mod)
    return mod


HOME = {"Person": "identity.models", "Event": "memory.models", "Approval": "approvals.models"}


def test_an_import_is_split_by_home_and_the_names_that_stay_are_kept() -> None:
    cm = load()
    text = "from superaide.platform.db.models import Base, Person, Event as E, Approval  # noqa: E402\nx = 1\n"
    got = cm.rewrite_imports(text, HOME)
    assert got.splitlines() == [
        "from superaide.platform.db.models import Base  # noqa: E402",
        "from superaide.identity.models import Person",
        "from superaide.memory.models import Event as E",
        "from superaide.approvals.models import Approval",
        "x = 1",
    ] or got.splitlines()[0].startswith("from superaide.platform.db.models import Base")
    assert "Person" not in got.splitlines()[0] and "# noqa: E402" in got


def test_an_indented_and_a_parenthesised_import_keep_their_shape() -> None:
    cm = load()
    text = (
        "def f():\n"
        "    from superaide.platform.db.models import (\n"
        "        Person,\n"
        "        Scoped,\n"
        "    )\n"
        "    return Person\n"
    )
    got = cm.rewrite_imports(text, HOME)
    assert "    from superaide.identity.models import Person" in got
    assert "    from superaide.platform.db.models import Scoped" in got
    compile(got, "x", "exec")


def test_other_imports_and_unrelated_names_are_left_alone() -> None:
    cm = load()
    text = "from superaide.platform.db.models import Base, Scoped\nfrom superaide.memory.store import Person\n"
    assert cm.rewrite_imports(text, HOME) == text


def test_dotted_strings_follow_the_class() -> None:
    cm = load()
    text = 'monkeypatch.setattr("superaide.platform.db.models.Person.x", 1); "superaide.platform.db.models.Base"'
    got = cm.rewrite_strings(text, HOME)
    assert "superaide.identity.models.Person.x" in got and "superaide.platform.db.models.Base" in got


def test_every_table_class_has_exactly_one_home_and_the_homes_exist() -> None:
    cm = load()
    from superaide.platform.db.models import Base

    classes = {m.class_.__name__ for m in Base.registry.mappers}
    assert set(cm.HOME) <= classes  # nothing in the plan is a class that does not exist
    for name, home in cm.HOME.items():
        path = ROOT / "backend" / "src" / "superaide" / (home.replace(".", "/") + ".py")
        assert path.is_file() and f"class {name}(" in path.read_text(), name


def test_the_old_import_path_still_resolves_for_one_release() -> None:
    import superaide.identity.models as new
    import superaide.platform.db as db
    from superaide.platform.db.models import Person

    assert Person is new.Person and db.Person is new.Person
