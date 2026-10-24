# Dashboard information architecture (issue #441)

Status: **proposal, waiting for the owner's approval.** Nothing in this note is built yet. It answers four questions: where each page lives today and where it should live, what the short top-level menu looks like, what the one shared settings component is, and what the screens look like. The mock-ups are screenshots of a throwaway static page (not the product), in the PR that adds this note.

Owner's request (24 Oct 2026): the signature setting should not be its own top-level page, it belongs under Gmail: "a separate page for a specific capability setting, like `/dashboard/capabilities/gmail/config`".

## 1. The rule

1. **A capability owns its settings.** `/dashboard/capabilities/<capability>` is its overview and connection; `/dashboard/capabilities/<capability>/<setting>` is one group of its settings. A new feature that configures a capability adds a sub-page, never a new top-level menu item.
2. **One settings pattern** everywhere (section 4).
3. **A short top-level menu**: pages the owner uses daily, plus one entry per area (Learning, Capabilities, Connections), not one per feature.
4. **No dead links.** Every old URL redirects permanently (308) to its new place, query string kept.

## 2. Route map

### 2.1 Today (frontend/src/app/(site)/dashboard/**)

| Route | What it is | Menu item today |
| --- | --- | --- |
| `/dashboard` | Home | yes |
| `/dashboard/chat` | Chat | yes |
| `/dashboard/needs-you` | triage inbox (flag `triage`) | yes |
| `/dashboard/approvals` | requests waiting for the owner's yes | yes |
| `/dashboard/reminders` | reminders | yes |
| `/dashboard/notebook` (+ `brain`, `episodes`, `journal`, `mirror`) | what Ava remembers | yes (sub-pages are linked inside) |
| `/dashboard/people` | contacts and trust | yes |
| `/dashboard/rehearsal` | rehearsal room (flag `rehearsal`) | yes |
| `/dashboard/school` | Ava School (flag `school`) | yes |
| `/dashboard/principal-card` | the email sign-off (flag `card`) | yes, but it only controls how Gmail emails are signed |
| `/dashboard/capabilities` | one card per capability, each with its panel inline | yes |
| `/dashboard/skills` | server-side redirect to `capabilities` | no |
| `/dashboard/connections` | Telegram, Slack, Google Calendar OAuth client, voice keys, WhatsApp note | yes |
| `/dashboard/models` | AI model (providers, roles, credits) | yes |
| `/dashboard/settings` | account, password, email, ask link, access log | yes |

Server-side links that point into the dashboard (backend): `/dashboard/capabilities` (Google sign-in return, `?google=` and `?gmail=`), `/dashboard/connections`, `/dashboard/reminders`, `/dashboard/notebook`, `/dashboard/needs-you`, `/dashboard/models`, `/dashboard/chat`, `/dashboard/approvals` (push and notification links). 14 menu items today.

### 2.2 Proposed, old to new

| Old | New | Redirect |
| --- | --- | --- |
| `/dashboard/capabilities` | stays: an **index** (status, connect/disconnect, one line per capability) | none |
| `/dashboard/capabilities#gmail` (and `#calendar`...) | `/dashboard/capabilities/gmail` | the page reads the hash on load and `router.replace`s (a hash never reaches the server) |
| `/dashboard/capabilities?gmail=connected\|no_access\|api_disabled\|failed` | `/dashboard/capabilities/gmail?gmail=...` | server redirect, query kept; the Gmail OAuth `BACK` URL changes to the new path, the old one keeps working |
| `/dashboard/capabilities?google=...` (calendar sign-in) | `/dashboard/capabilities/calendar?google=...` | same |
| `/dashboard/principal-card` | `/dashboard/capabilities/gmail/signature` | 308, in `next.config.ts` `redirects()` |
| `/dashboard/skills` | `/dashboard/capabilities` | exists |
| new `/dashboard/capabilities/gmail` | Gmail overview: connection and links to its settings | |
| new `/dashboard/capabilities/gmail/reading` | what Ava may read (#437) | |
| new `/dashboard/capabilities/gmail/signature` | Principal card and the HTML signature (#135, #439) | |
| new `/dashboard/capabilities/gmail/drafts` | whether she may prepare drafts | |
| new `/dashboard/capabilities/calendar` (+ `/actions`) | Google Calendar: connection, calendar actions | |
| new `/dashboard/capabilities/web-research`, `/reminders`, `/memory` | overview only until they have settings | |
| `/dashboard/school` | `/dashboard/learning/school` | 308 |
| `/dashboard/rehearsal` | `/dashboard/learning/rehearsal` | 308 |
| new `/dashboard/learning` | index: Ava School, the rehearsal room and the capability releases in one place | |
| `/dashboard/connections`, `/models`, `/settings`, `/notebook/**`, `/chat`, `/needs-you`, `/approvals`, `/reminders`, `/people` | unchanged | none |

Pushes, emails and docs only use the unchanged routes and `/dashboard/capabilities`, so no external link breaks. A test crawls the menu and every row of this table (old URL answers 308 and lands on a page with its h1; no menu link answers 404).

## 3. Top-level menu

Proposed (12 items, was 14; Learning shows only while School or Rehearsal is on):

```
Your assistant      Home · Chat · Needs you · Approvals · Reminders · Notebook · People
Learning            Learning   (Ava School, Rehearsal room, capability releases)
Setup               Capabilities · Connections · AI model · Settings
```

- **Learning** groups what teaches or tests Ava: School, Rehearsal, and the capability releases panel that lives at the bottom of Capabilities today.
- **Connections** keeps the *channels* (Telegram, Slack, web) and the account keys that are not tied to one capability (voice services). Google sign-in details move into Capabilities, where Gmail and Calendar are.
- **Capabilities** are the *tools* (Gmail, Calendar, web research, reminders, memory), each with its own settings.
- The Principal card item disappears: it is Gmail > Signature.

| | Pros | Cons |
| --- | --- | --- |
| Group Learning | three features owners rarely open become one entry; room for more | one more click to reach the rehearsal room |
| Settings under the capability | the owner finds the signature under Gmail; the Gmail card stops growing; a pattern for #433's connectors | a deeper URL; needs redirects and a sub-nav |
| Keep Connections for channels only | the word means one thing | the Google keys move, so a short pointer is needed on Connections |
| Option kept open: AI model under Settings | 11 items | hides the one setting Ava cannot work without; **not proposed** |

Real example. Owner wants to change the email sign-off. Today: Principal card (top-level, no mention of Gmail). Proposed: Capabilities > Gmail > Signature, with breadcrumbs `Capabilities / Gmail / Signature`, and the old bookmark still lands there.

## 4. One settings pattern: `SettingsSection` and `SettingRow`

Used by Gmail reading, signature and drafts first, then every capability. Lives in `frontend/src/components/settings/`.

`SettingsSection` props: `title`, `description` (one sentence), `children` (rows). Renders a bordered card with a tinted header, an `h2`, and a list (`ul`) of rows.

`SettingRow` props:

| Prop | Meaning |
| --- | --- |
| `title` | what it is, in the owner's words (the control's accessible name) |
| `why` | one sentence: what this setting is for |
| `example` | a real example with the fictional names from `docs/design/example-names.md` ("Priya (Partner) writes about Thursday: Ava can use it in her answer.") |
| `status` | `on`, `off`, `default` or a short text; shown as a badge, `Off (default)` when untouched |
| `control` | a switch, a select, a text field or a button, always labelled by `title` |
| `changes` | "If you turn this on: ..." what actually changes, in plain words |
| `audit` | `{ by: 'you' \| 'Ava' \| 'operator', at: ISO }` or none, shown as "Last changed by you, 22 Oct 09:12" or "Never changed" |
| `danger` | revoke or delete variant (red control, confirm step) |

Data: each settings endpoint returns `last_changed: { by, at } \| null` read from the existing audit log (`AuditLog`, written on every owner setting change), so no new table. Rows whose setting has no audit entry say "Never changed".

Rules:
- **Keyboard**: every control is reachable by Tab in reading order, switches are `role="switch"` operable with Space, Enter on buttons; focus ring from the global `:focus-visible`; after a change focus stays on the control and a polite status ("Saved") is announced once.
- **390 px**: single column, the control moves under the text, 44 px minimum targets (`touch:min-h-11`), no horizontal scroll; breadcrumbs wrap, the sub-nav becomes horizontally scrollable tabs.
- **Saving**: a change saves at once with an inline "Saved" and "Undo" for 5 s; errors use the existing `ActionError`. Loading uses the ADR 0014 skeleton, empty states say what to do next.
- **Tokens**: existing colors, radii, type scale; icons from the brand set. No new colors.
- **Layout**: a capability layout (`capabilities/[capability]/layout.tsx`) draws breadcrumbs, the title and the sub-nav (left on desktop, tabs on a phone) once, so every sub-page only renders its sections. Sub-nav items come from the capability's manifest, not hard-coded.

## 5. Mock-ups

Screenshots of a throwaway static page, desktop 1280 and 390 px, for: Capabilities index, Gmail overview, Gmail / Reading, Gmail / Signature. They are in the PR description (evidence branch `pr-ia-note/`). They show the intended hierarchy and spacing, not final styling.

## 6. Delivery (after approval)

Small PRs, each green first, each with e2e (old URLs redirect; axe; desktop and 390 px) and real before/after screenshots:

1. Shared `SettingsSection` / `SettingRow` and the capability layout (sub-nav, breadcrumbs), unit and axe tests, nothing moved yet.
2. Capability routes (`/capabilities/<name>`, index) with redirects and a crawl test of every old URL.
3. Move the Principal card to `gmail/signature`; delete the menu item; redirect.
4. Convert Gmail reading (#437) and drafts to the new rows (after #437 and #439 merge, so the files do not collide).
5. Navigation regrouping (Learning) only if the owner approves section 3.

## 7. Open questions for the owner

- Approve the 12-item menu with a **Learning** group? Or keep School and Rehearsal where they are and only move the Principal card?
- Should the Google OAuth client (client id and secret) move into Capabilities (Gmail and Calendar share it) or stay on Connections?
- "Last changed by/when" needs each settings endpoint to return it: fine to add it as each capability is converted?
