"""Dump the FastAPI OpenAPI schema to backend/openapi.snapshot.json, or check it is current.

    uv run python scripts/openapi_snapshot.py          # rewrite the snapshot (make openapi)
    uv run python scripts/openapi_snapshot.py --check  # exit 1 if the live schema differs

The snapshot is the API contract. CI runs the full browser suite when it changes (docs/decisions/0013),
and runs only a short smoke subset for backend-only changes that leave it alone.
"""

import json
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "openapi.snapshot.json"


def live_schema() -> str:
    from superaide.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    live = live_schema()
    if "--check" in sys.argv:
        if SNAPSHOT.exists() and SNAPSHOT.read_text() == live:
            return 0
        print("openapi.snapshot.json is out of date; run `make openapi` and commit it.", file=sys.stderr)
        return 1
    SNAPSHOT.write_text(live)
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
