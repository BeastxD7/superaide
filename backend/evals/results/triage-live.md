# Triage report (live)

52 labelled messages (synthetic). Cost US$0.0084.
Measured on the shadow (Child) decision for Infant labels, else the decision (ADR 0010 section 8). Interrupts that reached the owner at the labels' stage: 9.

| Measure | Value |
|---|---:|
| Acceptable decisions | 96% |
| Tell-now precision (interrupt or defer) | 92% (11 right, 1 false) |
| Tell-now recall | 85% (2 missed) |
| Verify recall (risky requests caught) | 100% |

| Expected | handle | hold | interrupt | defer | verify |
|---|---:|---:|---:|---:|---:|
| handle | 0 | 0 | 0 | 0 | 0 |
| hold | 0 | 27 | 1 | 0 | 2 |
| interrupt | 0 | 1 | 10 | 0 | 1 |
| defer | 0 | 0 | 0 | 1 | 0 |
| verify | 0 | 0 | 0 | 0 | 9 |

## Not acceptable

- tl-009: expected hold, got interrupt (VIP name on an unverified account)
- tl-030: expected hold, got verify (legal, not urgent)
