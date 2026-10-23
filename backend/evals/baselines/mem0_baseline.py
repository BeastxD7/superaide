"""Opt-in: run Ava's memory questions against the open-source Mem0 on the same notes (#51).

Never part of the product and never run in CI. Mem0 is an optional extra that is not installed by default:

    cd backend
    uv sync --extra eval-mem0
    EVAL_DATABASE_URL=postgresql+psycopg://.../agent_test_evals uv run python evals/baselines/mem0_baseline.py

It needs no API key and sends nothing anywhere: the notes are stored raw (`infer=False`, so no language
model reads them), embedded locally with the same model Ava uses, and kept in a throwaway on-disk store.
The scenario worlds are made-up test data, never an owner's. The result is written to
`backend/evals/results/baseline-mem0.md`. Pass `--config file.json` to give Mem0 your own configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
OUT = BACKEND / "evals" / "results" / "baseline-mem0.md"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # what Ava embeds with
USER = "eval"


def default_config(path: str) -> dict:
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {"collection_name": "ava_eval", "path": path, "embedding_model_dims": 384, "on_disk": True},
        },
        "embedder": {"provider": "fastembed", "config": {"model": EMBED_MODEL, "embedding_dims": 384}},
        # Mem0 wants an LLM configured even when it is not used; with infer=False it is never called, so this key
        # is a placeholder and no request is ever made.
        "llm": {"provider": "openai", "config": {"model": "gpt-4o-mini", "api_key": "placeholder-never-used"}},
        "history_db_path": str(Path(path) / "history.db"),
    }


class Mem0Memory:
    name = "Mem0 (open source)"

    def __init__(self, config: dict) -> None:
        from mem0 import Memory

        self.config, self.mem = config, Memory.from_config(config)

    def reset(self) -> None:
        self.mem.reset()

    def add(self, notes: list[str]) -> None:
        for n in notes:
            self.mem.add(n, user_id=USER, infer=False)

    def search(self, query: str, k: int) -> list[str]:
        from superaide.evals.baseline import results_text

        return results_text(self.mem.search(query, top_k=k, filters={"user_id": USER}))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="a JSON file with a Mem0 config (default: local, no key, no network)")
    args = p.parse_args(argv)
    if os.environ.get("CI"):
        sys.exit("The Mem0 baseline is opt-in and never runs in CI.")
    try:
        import mem0  # noqa: F401
    except ImportError:
        sys.exit("Mem0 is not installed (it is an optional extra): run `uv sync --extra eval-mem0` first.")
    os.environ.setdefault("MEM0_TELEMETRY", "False")  # nothing leaves this machine
    from superaide.evals.__main__ import _env, args_for_offline

    _env(args_for_offline())  # points settings at the evals database (wiped for every scenario)
    from superaide.evals.baseline import compare, render
    from superaide.evals.gate import ltm_scenarios

    work = tempfile.mkdtemp(prefix="mem0-eval-")
    try:
        config = json.loads(Path(args.config).read_text()) if args.config else default_config(work)
        system = Mem0Memory(config)
        rows = compare(ltm_scenarios(), system)
        text = render(rows, system.name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    OUT.write_text(text)
    print(text)
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
