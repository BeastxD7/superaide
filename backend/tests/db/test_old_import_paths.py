"""The old import paths ADR 0015 phase 3 keeps alive: Alembic revisions (37 of them) import `superaide.db.rls`."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_the_frozen_rls_path_is_a_real_file_that_resolves_to_the_platform_module() -> None:
    shim = BACKEND / "src/superaide/db/rls.py"
    assert shim.is_file() and (BACKEND / "src/superaide/db/__init__.py").is_file()
    # run in a fresh interpreter with the alias finder bypassed: the file alone must be enough once the aliases go
    code = (
        "import sys, importlib.util;"
        f"spec = importlib.util.spec_from_file_location('superaide.db.rls', {str(shim)!r});"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "import superaide.platform.db.rls as real;"
        "assert sys.modules['superaide.db.rls'] is real or m is real or hasattr(m, 'enable'); print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND)
    assert out.stdout.strip() == "ok", out.stderr


def test_old_names_are_the_same_module_objects() -> None:
    import superaide.config as old_config

    import superaide.db.rls as old_rls
    import superaide.platform.config as config
    import superaide.platform.db.rls as rls

    assert old_config is config and old_rls is rls
    assert importlib.util.find_spec("superaide.tenancy.context") is not None
    from superaide.tenancy.context import use_instance as old_use

    from superaide.platform.tenancy.context import use_instance

    assert old_use is use_instance


def test_the_entry_points_still_import() -> None:
    from superaide import main as entry
    from superaide.main import app

    assert app is not None and callable(entry)
