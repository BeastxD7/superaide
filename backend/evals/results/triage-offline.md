# Triage report (offline, rules only)

52 labelled messages (synthetic). Cost US$0.0000.
Measured on the shadow (Child) decision for Infant labels, else the decision (ADR 0010 section 8). Interrupts that reached the owner at the labels' stage: 5.

| Measure | Value |
|---|---:|
| Acceptable decisions | 96% |
| Tell-now precision (interrupt or defer) | 100% (5 right, 0 false) |
| Tell-now recall | 38% (8 missed) |
| Verify recall (risky requests caught) | 100% |

| Expected | handle | hold | interrupt | defer | verify |
|---|---:|---:|---:|---:|---:|
| handle | 0 | 0 | 0 | 0 | 0 |
| hold | 0 | 29 | 0 | 0 | 1 |
| interrupt | 0 | 7 | 5 | 0 | 0 |
| defer | 0 | 1 | 0 | 0 | 0 |
| verify | 0 | 0 | 0 | 0 | 9 |

## Not acceptable

- tl-015: expected interrupt, got hold (urgent topic + deadline)
- tl-047: expected interrupt, got hold (legal, imminent)
