# Time to first useful reply

How long a new owner takes from "nothing" to a first useful reply from Ava, measured every night on a fresh database (#411).

## Targets

| Path | Target | What counts |
| --- | --- | --- |
| Hosted | under 3 minutes (180 s) | the browser journey alone: nothing to install |
| Self-hosted | under 10 minutes (600 s) | cold start (install, build, migrate, start) plus the same journey |

The nightly run fails when a target is missed. The person in the journey is a script: it never reads or hesitates, so
the number is the floor that the product's own speed sets. A human adds reading and typing time on top, and, when
self-hosting, the time to get an AI key from the provider, which no script can measure.

## Stages

Journey (`e2e/tests/journeys/time-to-first-reply.ttfr.spec.ts`, hosted stack with a fake Anthropic):
`start`, `app_reachable`, `sign_up`, `hire`, `brain_connected` (Superaide AI credits chosen), `introduction_done`
(the dashboard opens only after it), `first_reply` (the model's answer to the first chat message).

Cold start (`e2e/scripts/ttfr-cold-start.mjs`): `install_backend`, `install_frontend`, `build_frontend`,
`database_ready` (fresh database, all migrations), `api_up`, `web_up`.

## How it runs

- Nightly: `.github/workflows/ttfr.yml` (also on demand). Results are the `ttfr-results` artifact; each run's row joins
  the `ttfr-history` artifact of the run before it (last 90), and the job summary shows the last 14 as a trend.
- Locally: `E2E_TTFR=1 E2E_BRAIN=1 E2E_SLOT=<n> npx playwright test --project=ttfr` in `e2e/`, then
  `node scripts/ttfr-cold-start.mjs` and `node scripts/ttfr-report.mjs`.
- Output is numbers only: no names, emails or tokens.

## First measurement (2026-11-19, developer Mac, warm package caches, so a lower bound for the cold start)

| Stage | Seconds |
| --- | ---: |
| app reachable | 0.6 |
| sign up | 2.5 |
| hire | 1.4 |
| brain connected | 6.5 |
| introduction done | 37.4 |
| first reply | 2.6 |
| **Hosted total** | **51** |
| Cold start (install, build, migrate, start) | 56 |
| **Self-hosted total** | **about 107** |

## Top blockers found

1. The first model reply is behind the introduction: 37 of the 51 seconds are Ava's scripted steps, and the chat is not
   open until they end.
2. The sign-up form can be submitted before the page is interactive, which ends on a blank browser error page instead
   of the hire step (seen while scripting the journey; the script now waits for the page to settle).
3. Self-hosting needs an AI key obtained outside the app before the first reply; the machine time is small
   (about a minute) but this human step is not measured at all.

Filed as #599, #600 and #601; fixing them is out of scope here.
