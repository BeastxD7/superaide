# Changelog

Every change to `main` arrives through a pull request, so this is the audit trail of what changed, when and why.
Each line links to its pull request, which has the review notes, the before and after screenshots and the test output.

How it is kept: one entry per merged pull request, newest first, grouped by day and type (Added, Fixed, Tests, ...).
After you merge, run `scripts/changelog.py` (it rewrites this file from GitHub's merged pull requests), and publish a
GitHub Release with generated notes (`gh release create vYYYY.MM.DD --generate-notes`, grouped by `.github/release.yml`).

## Unreleased

### 2026-10-22

**Tests**
- test(e2e): isolation test checks A's secret words, not digits a random id can contain ([#161](https://github.com/BeastxD7/superaide/pull/161))
- test(web): API proxy contract, reply safety, security headers (#81) ([#152](https://github.com/BeastxD7/superaide/pull/152))
- test(e2e): the long-link chat test no longer depends on reply timing ([#145](https://github.com/BeastxD7/superaide/pull/145))
- test(digest): the morning digest tests no longer depend on the time of day ([#107](https://github.com/BeastxD7/superaide/pull/107))
- test(api): guard that a streamed chat turn stays in its owner's instance (#94) ([#106](https://github.com/BeastxD7/superaide/pull/106))
- test(triage): make the catch-up tests independent of the time of day ([#100](https://github.com/BeastxD7/superaide/pull/100))

**Fixed**
- fix(web): form validation, confirmations, stale approvals, redirects, dates, initials, dev verify link (#70 #77 #79 #97) ([#148](https://github.com/BeastxD7/superaide/pull/148))
- fix(jobs): recurring chains survive failures, stale claims and orphaned spend are recovered, turns can't starve the pool (#84 #86 #91) ([#158](https://github.com/BeastxD7/superaide/pull/158))
- fix(security): prompt contents, link-code names, handle probing, token logging (#93) ([#160](https://github.com/BeastxD7/superaide/pull/160))
- fix(web): contrast, 16px inputs, long-text wrapping, Needs-you toggles, focus, axe checks (#78) ([#151](https://github.com/BeastxD7/superaide/pull/151))
- fix(knowledge): a document removed while Ava reads it leaves no live facts (#87) ([#159](https://github.com/BeastxD7/superaide/pull/159))
- fix(brain): a rejected AI key is permanent for the run, visible to the owner, and cleared by a working one (#150) ([#157](https://github.com/BeastxD7/superaide/pull/157))
- fix(models): credits can no longer be priced at zero, budget messages and limits (#59 to #68) ([#102](https://github.com/BeastxD7/superaide/pull/102))
- fix: show errors and retries when the dashboard, Connections and hire page fail to load or act (#23 #35 #38-#43) ([#149](https://github.com/BeastxD7/superaide/pull/149))
- fix(web): chat stream cut, IME Enter, live region, Needs-you recovery, console conversation race (#74 #76) ([#144](https://github.com/BeastxD7/superaide/pull/144))
- fix(asklink): a suggested handle always passes the handle rules ([#143](https://github.com/BeastxD7/superaide/pull/143))
- fix(auth): a new account belongs to no Ava until it hires (#24 #25) ([#103](https://github.com/BeastxD7/superaide/pull/103))
- fix(web): tell "couldn't load" from "not hired" and "signed out"; handle a session that ends ([#99](https://github.com/BeastxD7/superaide/pull/99))
- fix(web): hero orb label, phone band and sidebar sheet, emoji initials (#36, #37, #32, #33) ([#96](https://github.com/BeastxD7/superaide/pull/96))
- fix: backend reliability (hosted mode in prod, reminders list, max_tokens, upload, small date and config bugs) ([#104](https://github.com/BeastxD7/superaide/pull/104))
- fix(auth): email change, Google sign-in and reset-link fixes (#18 #20 #26 #27 #28 #29 #30 #31) ([#101](https://github.com/BeastxD7/superaide/pull/101))
- fix(knowledge): read shared documents when the brain becomes ready, by any path ([#95](https://github.com/BeastxD7/superaide/pull/95))

**Added**
- feat(calendar): push updates from Google Calendar (events.watch), behind GOOGLE_CALENDAR_PUSH ([#154](https://github.com/BeastxD7/superaide/pull/154))
- feat(calendar): free/busy and calendar actions (add, move, decline) with consent and approvals ([#147](https://github.com/BeastxD7/superaide/pull/147))
- feat(onboarding): set up Ava's brain first, then read files and links live ([#155](https://github.com/BeastxD7/superaide/pull/155))
- feat(approvals): bind a Gmail send approval to the content the owner saw (#111) ([#156](https://github.com/BeastxD7/superaide/pull/156))
- feat(gmail): inbox triage, the digest's email line, and Gmail settings in Capabilities ([#146](https://github.com/BeastxD7/superaide/pull/146))
- feat(voice): hands-free voice introduction call behind VOICE_INTRO_V2 (ADR 0012) ([#108](https://github.com/BeastxD7/superaide/pull/108))

**Dependencies**
- build(deps): Bump py-vapid from 1.9.4 to 1.9.6 in /backend in the python-minor-and-patch group ([#17](https://github.com/BeastxD7/superaide/pull/17))
- build(deps): Bump docker/build-push-action from 6 to 7 ([#16](https://github.com/BeastxD7/superaide/pull/16))
- build(deps): Bump gitleaks/gitleaks-action from 2 to 3 ([#15](https://github.com/BeastxD7/superaide/pull/15))
- build(deps): Bump the npm-minor-and-patch group across 2 directories with 2 updates ([#10](https://github.com/BeastxD7/superaide/pull/10))

**Documentation**
- docs(research): AI era research reports on learning, protocols and interaction ([#109](https://github.com/BeastxD7/superaide/pull/109))

**Changed**
- Frontend hardening: no remote images, CSP and HSTS, error screens, console fixes ([#105](https://github.com/BeastxD7/superaide/pull/105))

**Maintenance**
- chore(github): consistent issue forms, PR template, labels and community files ([#11](https://github.com/BeastxD7/superaide/pull/11))

