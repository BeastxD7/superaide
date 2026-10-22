# Triage report (live)

52 labelled messages (synthetic). Cost US$0.0045.
Measured on the shadow (Child) decision for Infant labels, else the decision (ADR 0010 section 8). Interrupts that reached the owner at the labels' stage: 8.

| Measure | Value |
|---|---:|
| Acceptable decisions | 96% |
| Tell-now precision (interrupt or defer) | 100% (10 right, 0 false) |
| Tell-now recall | 77% (3 missed) |
| Verify recall (risky requests caught) | 100% |

| Expected | handle | hold | interrupt | defer | verify |
|---|---:|---:|---:|---:|---:|
| handle | 0 | 0 | 0 | 0 | 0 |
| hold | 0 | 27 | 0 | 0 | 3 |
| interrupt | 0 | 0 | 9 | 0 | 3 |
| defer | 0 | 0 | 0 | 1 | 0 |
| verify | 0 | 0 | 0 | 0 | 9 |

## Not acceptable

- tl-015: expected interrupt, got verify (urgent topic + deadline)
- tl-030: expected hold, got verify (legal, not urgent)
