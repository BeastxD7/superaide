"""`uv run python -m superaide.evals run [--category X] [--live] [--k 3]`, or `... triage [--live]`

Offline (the default) uses the fake model and costs nothing: it checks the harness and grades
everything that doesn't depend on what the model writes. `--live` uses the real chat model with
the API key from backend/.env (or --env-file); every call, the judge's too, goes through
BudgetGuard with a hard cap (default US$1.00) counted over the evals database's whole ledger.

The database in EVAL_DATABASE_URL (default agent_test_evals) is wiped for every scenario.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
DEFAULT_DB = "postgresql+psycopg://agent:agent@localhost:5432/agent_test_evals"


def _env(args: argparse.Namespace) -> str:
    """Point settings at the evals database before anything reads them. Returns the API key
    (live only), which is handed to the model client directly and never put in the environment."""
    from dotenv import dotenv_values

    env_file = Path(args.env_file) if args.env_file else BACKEND / ".env"
    values = dotenv_values(env_file) if env_file.exists() else {}
    db = os.environ.get("EVAL_DATABASE_URL") or DEFAULT_DB
    if db in {values.get("DATABASE_URL"), os.environ.get("DATABASE_URL")} and "eval" not in db:
        sys.exit("EVAL_DATABASE_URL must not be your real database: every scenario wipes it")
    os.environ.update(
        {
            "DATABASE_URL": db,
            "FAKE_LLM": "true",  # the harness passes the model in itself
            "ANTHROPIC_API_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "SLACK_BOT_TOKEN": "",
            "SLACK_TEAM_ID": "T_OURS",
            "AGENT_NAME": "Ava",
            "WEB_TOOLS_ENABLED": "false",
            "MAX_SPEND_USD": str(args.cap),
            "RUN_CHANNELS_IN_API": "false",
            "RUN_JOBS_IN_API": "false",
        }
    )
    key = str(values.get("ANTHROPIC_API_KEY") or "").strip()
    if args.live and not key.startswith("sk-ant-"):
        sys.exit(f"--live needs ANTHROPIC_API_KEY in {env_file}")
    return key if args.live else ""


def args_for_offline() -> argparse.Namespace:
    return argparse.Namespace(env_file=None, live=False, cap=0.0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m superaide.evals")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run scenarios and write a scorecard")
    r.add_argument("--category", action="append", help="only this category (repeatable)")
    r.add_argument("--id", action="append", help="only this scenario id (repeatable)")
    r.add_argument("--limit", type=int, help="only the first N scenarios (after filters)")
    r.add_argument("--skip-known-gaps", action="store_true", help="leave out scenarios marked known_gap")
    r.add_argument("--priority", help="categories to run first, comma-separated (if the cap cuts the run short)")
    r.add_argument("--live", action="store_true", help="use the real model (costs money)")
    r.add_argument("--k", type=int, default=1, help="runs per scenario (default 1; use 3 for pass^k)")
    r.add_argument("--cap", type=float, default=1.00, help="hard spend cap in US$ over the evals ledger")
    r.add_argument("--env-file", help="where the API key is (default backend/.env)")
    r.add_argument("--name", help="scorecard file name (default scorecard-<mode>)")
    r.add_argument("--wipe-ledger", action="store_true", help="forget earlier eval spend (resets the cap)")
    sub.add_parser("list", help="list scenarios")
    v = sub.add_parser("voice-vs-text", help="30 tasks by text and by voice, offline (#125): how often voice diverges")
    v.add_argument("--out", help="where to write the JSON report (default backend/evals/results)")
    g = sub.add_parser("gate", help="long-term-memory regression gate: offline, fails if a scenario regresses")
    g.add_argument("--update", action="store_true", help="record today's results as the baseline")
    t = sub.add_parser("triage", help="triage precision and recall on the labelled set (ADR 0010)")
    t.add_argument("--live", action="store_true", help="use the triage model (costs money)")
    t.add_argument("--cap", type=float, default=0.25, help="hard spend cap in US$ over the evals ledger")
    t.add_argument("--env-file", help="where the API key is (default backend/.env)")
    t.add_argument("--name", help="report file name (default triage-<mode>)")
    t.add_argument("--out", help="where to write the report (default backend/evals/results)")
    t.add_argument("--replay", help="re-decide with the classifier answers saved in this report (free)")
    args = p.parse_args(argv)

    if args.cmd == "triage":
        from superaide.evals import triage_report

        key = _env(args) if args.live else ""
        s = triage_report.run(
            live=args.live, key=key, cap=args.cap, name=args.name or "",
            out_dir=Path(args.out) if args.out else None, replay=Path(args.replay) if args.replay else None,
        )  # fmt: skip
        tell = s["tell_now"]
        print(
            f"{s['labels']} labels, {s['mode']}: acceptable {s['acceptable']}, tell-now precision "
            f"{tell['precision']} recall {tell['recall']}, verify recall {s['verify_recall']}, "
            f"US${s['cost_usd']:.4f}"
        )
        return 0

    if args.cmd == "voice-vs-text":
        from superaide.evals import voice_vs_text

        _env(args_for_offline())
        return voice_vs_text.main(args.out)

    if args.cmd == "gate":
        from superaide.evals.gate import run_gate

        _env(args_for_offline())
        return run_gate(update=args.update)

    if args.cmd == "list":
        from superaide.evals.scenario import load_all

        for sc in load_all():
            print(f"{sc.category:14} {sc.severity:9} {sc.id}")
        return 0

    key = _env(args)
    return run(args, key)


def run(args: argparse.Namespace, key: str) -> int:
    from decimal import Decimal

    from superaide.brain.llm import LLM, ClaudeLLM
    from superaide.brain.spend.budget import BudgetGuard
    from superaide.evals import report
    from superaide.evals.harness import Judge, Run, ledger_total, make_judge, reset_db, run_scenario
    from superaide.evals.scenario import load_all
    from superaide.platform.config import get_settings

    everything = load_all()  # once: the files may change while a long run is going
    scenarios = list(everything)
    if args.category:
        scenarios = [s for s in scenarios if s.category in set(args.category)]
    if args.id:
        scenarios = [s for s in scenarios if s.id in set(args.id)]
    if args.priority:
        first = [c.strip() for c in args.priority.split(",")]
        scenarios.sort(key=lambda s: first.index(s.category) if s.category in first else len(first))
    if args.skip_known_gaps:
        scenarios = [s for s in scenarios if not s.known_gap]
    if args.limit:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        print("no scenarios match")
        return 1

    settings = get_settings()
    reset_db(wipe_ledger=args.wipe_ledger)
    inner: LLM | None = None
    judge: Judge | None = None
    if args.live:
        guard = BudgetGuard(args.cap)  # lifetime cap over this database's ledger; no daily limits
        claude = ClaudeLLM(guard, api_key=key)
        inner = claude
        judge = make_judge(claude, settings.model_triage)
        before = guard.summary()
        print(f"evals ledger: US${before.settled_usd:.4f} spent of the US${args.cap:.2f} cap")

    spent_before = ledger_total()
    runs: dict[str, list[Run]] = {}
    stop = False
    for sc in scenarios:
        for i in range(args.k):
            result = run_scenario(sc, inner=inner, judge=judge)
            runs.setdefault(sc.id, []).append(result)
            mark = "PASS" if result.passed else "FAIL"
            skipped = sum(o.status == "skip" for o in result.outcomes)
            print(
                f"{mark} {sc.id} [{i + 1}/{args.k}] {result.graded} graded, {skipped} skipped, "
                f"US${result.cost:.4f}, {result.seconds:.1f}s"
            )
            for o in result.outcomes:
                if o.status == "fail":
                    print(f"     - {o.where}: {o.name}: {o.reason[:220]}")
            if result.error:
                print("     ! " + result.error.strip().splitlines()[-1])
            if result.budget_stop:
                print("spend cap reached: stopping")
                stop = True
                break
        if stop:
            break

    spent = ledger_total() - spent_before
    n_runs = sum(len(v) for v in runs.values())
    projection = ""
    if args.live and n_runs:
        per = spent / n_runs
        total = len(everything)
        projection = (
            f"Measured US${per:.4f} per scenario run; a full {total}×3 run projects to US${per * total * 3:.2f}."
        )
        print(projection)
    summaries = report.group(scenarios, runs)
    data = report.build(
        summaries, live=args.live, model=settings.model_chat if args.live else "fake", spent=Decimal(spent),
        projection=projection,
    )  # fmt: skip
    md, js = report.write(data, args.name or f"scorecard-{'live' if args.live else 'offline'}")
    o = data["overall"]
    print(f"\nmean {o['mean']}, pass^k {o['pass_k']} over {o['scenarios']} scenarios; US${spent:.4f}")
    print(f"scorecard: {md}\n           {js}")
    harness_errors = [r for v in runs.values() for r in v if r.error]
    return 1 if harness_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
