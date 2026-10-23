#!/usr/bin/env python3
"""Recompute every skill's trust tier from exported receipt logs, and check each log's hash chain.

Standard library only. No install, no database, no network:

    python3 backend/scripts/skill_tier.py export.json [more-exports.json ...] [--skill acme/notes] [--version 1.0.0]

An export is what GET /app/skill-receipts/export returns (one per Ava instance). Each file's chain is verified on
its own; if ANY receipt was changed, removed or reordered the script says where and exits 1. Otherwise it merges the
receipts and prints the tier of each skill version. The rules are read from the export (so you see the thresholds the
operator used) unless you pass --default-rules. The rule code itself is backend/src/superaide/skills/tier_rules.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

RULES_FILE = Path(__file__).resolve().parent.parent / "src" / "superaide" / "skills" / "tier_rules.py"


def load_rules_module():
    spec = importlib.util.spec_from_file_location("tier_rules", RULES_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["tier_rules"] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--skill")
    ap.add_argument("--version")
    ap.add_argument("--default-rules", action="store_true", help="ignore the rules written in the export")
    args = ap.parse_args(argv)
    tr = load_rules_module()

    merged: list[dict] = []
    rules = None
    for path in args.files:
        data = json.loads(path.read_text(encoding="utf-8"))
        receipts = data["receipts"] if isinstance(data, dict) else data
        ok, at, why = tr.verify_chain(receipts)
        if not ok:
            print(f"CHAIN BROKEN in {path}: receipt {at}: {why}", file=sys.stderr)
            return 1
        print(f"chain ok: {path} ({len(receipts)} receipts)", file=sys.stderr)
        merged += receipts
        if isinstance(data, dict) and data.get("rules") and not args.default_rules:
            rules = tr.Rules(**data["rules"])
    rows = [r for r in merged if (not args.skill or r["skill_id"] == args.skill)
            and (not args.version or r["version"] == args.version)]  # fmt: skip
    print(json.dumps(tr.compute_all(rows, rules or tr.Rules()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
