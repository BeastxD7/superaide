"""Scenario evaluations: how reliably Ava handles real owner usage.

Scenarios live in backend/evals/scenarios/*.yaml; `python -m superaide.evals run` drives each
through the real pipeline (Agent.handle) on a throwaway database and writes a scorecard to
backend/evals/results/. `make eval` runs it offline, `make eval-live` live.
"""
