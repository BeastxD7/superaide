# Test findings to fix

Bugs found by the test agents on 21 October 2026. Each one has a test of the correct behaviour that's marked as a known bug:
- `test.fail(true, 'BUG: …')` in `e2e/tests/`
- `@pytest.mark.xfail(strict=True)` in `backend/tests/`

To fix one:
1. Remove the marker.
2. Make the test pass.
3. Commit the fix and the test together.

The suite stays green in the meantime.

Severity: **high** means data could leak or the app breaks; **medium** is a real user-facing problem; **low** is polish.

## Accounts and access (`e2e/tests/accounts.spec.ts`)

| # | Severity | Bug | Test |
|---|---|---|---|
| A1 | Medium | A password containing your name as typed (with spaces) is accepted. `password_problem` strips spaces from the name but not from the password. | sign-up › a password that contains your name as you typed it is refused |
| A2 | Medium | A failed log-in isn't announced to screen readers: `Notice` has no `role="alert"` or live region. The smoke test's `getByRole('alert')` also matches Next's empty route announcer, so it should filter by text. | log-in › a failed log-in is announced to screen readers |
| A3 | Low | A 422 response for a password over 1024 characters sends the password back in `input`. No submitted secret should ever appear in a response. | sign-up › a refused sign-up or log-in never sends the password back |
| A4 | Low | Raw validator messages reach people: "String should have at least 3 characters" for a short email, and "at most 1024 characters" for a long password. | a very short email gets the same plain message… / a very long password gets the same plain message… |
| A5 | Low | A 500 with a plain-text body shows "Request failed with status code 500". | log-in › a server error shows a message a person can act on |
| A6 | Low | When the password error appears, the field keeps `aria-describedby` pointing at the removed hint, so the error isn't the field's description. | the password field error is announced as the field's description |
| A7 | Low | `/agents/<unknown>/hire` shows a hire page whose button does nothing, instead of the 404 page. | not-found pages › signed in › hiring an agent that does not exist shows the 404 page |

Product questions raised:
- Lockout and duplicate sign-up both reveal that an account exists.
- Should your first name alone in a password count as using your name?
- The 404 page keeps the site's default title.
- The "Can't reach Superaide… start.command" error wording is aimed at a developer, not a hosted user.

## Files, links and the video call (`e2e/tests/intro-sources*.spec.ts`)

| # | Severity | Bug | Test |
|---|---|---|---|
| F1 | Medium | A "partly readable" warning for a PDF with little text vanishes when Ava's reply re-mounts the panel. The file list never shows a document's warnings. | files › a warning about a partly readable file stays on screen |
| F2 | Medium | A refused file or link isn't announced to screen readers: `Notice` has no `role="alert"`. This is the same root cause as A2. | files › a refused file is announced to screen readers |
| F3 | Low–medium | Removing a file that's already gone fails silently. `remove()` in `steps.tsx` has no catch, so the row stays until a reload. | files › removing a file that was already removed elsewhere leaves a sane list |
| F4 | Low | A 500 with a non-JSON body shows "Request failed with status code 500". This is axios's fallback in `lib/http.ts`, and the same as A5. | files › a server error while uploading shows a message a person can act on |
| F5 | Low | A link over 2048 characters shows the validator's message instead of "That link is too long." | links › an over-long link gets a plain-language message |
| F6 | Low | Finishing the call twice posts two identical "Thanks for the call…" lines. | video call › finishing the call twice posts one summary, not two |
| F7 | Low | Ava's summary says "1 things": `_learned_summary` always uses the plural. | video call › after one answer, the summary says '1 thing' |
| F8 | Low | A file row says "1 facts". | files › a file that has been read shows how many facts came from it, in plain English |
| F9 | Low | The intro tabs don't follow the ARIA tabs pattern: no arrow keys, no roving tabindex, no `tabpanel`. | keyboard › the intro tabs follow the ARIA tabs pattern |
| F10 | Low | Tab labels are cut off at 412px. Commit `4d1b3f6` (short labels on phones) may already fix this. | mobile › the intro tab labels are readable in full on a phone |

Product questions raised:
- Restarting the call begins again at question 1 and adds duplicate facts.
- Leaving mid-call posts no acknowledgement.
- The call endpoints accept answers at any step and in any order.
- If speech recognition never starts, the call waits until the person clicks "Type instead".
- Dropping several files keeps only the first, without saying so.
- Offline, the summary reads "0 things … from 1 document".

## Hiring and the introduction (`e2e/tests/introduction*.spec.ts`)

| # | Severity | Bug | Test |
|---|---|---|---|
| I1 | Medium | A double-click on a step sends the answer twice: the fading-out panel (`AnimatePresence`) still takes clicks, so people can be saved twice and Ava repeats herself. | double submission › double-clicking a hello reply / "These are my people" sends it once |
| I2 | Medium | Concurrent answers each save everything (5 at once saves 5 Priyas). Needs `SELECT … FOR UPDATE` on `onboarding`, or a step compare-and-set. | the same answer sent several times at once is saved once (API) |
| I3 | Medium | An Indian browser reports `Asia/Calcutta`. Linux servers without legacy zone names reject it, and the code silently saves `Asia/Singapore`, so quiet hours end up 2.5 hours off. Add the `tzdata` package and store the canonical name. | reach: an Indian time zone, which Chrome reports as Asia/Calcutta… |
| I4 | Low–medium | Quiet hours with the same start and end (09:00–09:00) are accepted. | reach: the same start and end time is refused |
| I7 | Low | When a save fails, the composer has already cleared what you typed. | when saving fails, what you typed is still there to resend |
| I8 | Low (phone) | A long link in a chat bubble overflows the screen: the bubble needs `overflow-wrap: anywhere`. Same as W7. | mobile › a long link in your introduction wraps inside the screen |
| I9 | Low | Ava repeats a duplicated name ("Got it: Priya and Priya"). | people: Ava does not repeat a duplicated name back |
| I10 | Low | The same owner's overlapping hire requests get 409 "hired by someone else". | hire requests sent at the same moment by the same owner all succeed |
| I12 | Low | Step hints aren't announced to screen readers. Same root cause as A2 and F2. | the hint for a refused answer is announced to screen readers |

**Already fixed by the API agent's commits:** I5 (a name with `%` merging into another contact), I6 (posting step "done" again caused a 500) and I11 (a topic sent as text split into letters).

**Remove the `test.fail` markers for I5, I6 and I11** in `introduction.spec.ts`. Playwright fails a `test.fail` test that passes.

## Workspace, connections and console (`e2e/tests/workspace*.spec.ts`, `console.spec.ts`)

Written against the `/workspace` screen. Port the checks to `/dashboard/*` using the agent's re-test list below. The console tests carry over as they are.

| # | Severity | Bug | Test |
|---|---|---|---|
| W1 | Medium | A refused or failed message empties the composer and stays in the chat as if sent. This also applies to the dashboard chat. | a refused message keeps its draft and is not shown as sent |
| W2 | Medium | If the operator pauses Ava mid-session, the owner's next message gets silence. `/app/chat` returns `mode: "frozen"`, but the UI ignores it. | console › a pause made while the owner has the workspace open… |
| W3 | Medium | The owner's own sign-in shows as an identity failure on the console: `VERIFIED_LINK_METHODS` in `api/admin.py` lacks `account_signed_in`. | the owner's own sign-in is not counted as an identity failure |
| W4 | Medium | Logging in with `next=` can bounce through `/`. The login page follows `next` before `/app/me` loads, so the private page briefly sees no agent. Check this on `/dashboard` too. | logging in from /login?next=/workspace lands… even when the agent loads slowly |
| W5 | Medium | The simulator's "tell Ava a secret" step shows Telegram but sends as the owner's web account, so the made-up note lands in the real notebook. | console › a scenario sends on the channel its form shows |
| W6 | Low | A message sent while the history is still loading disappears when the late history arrives. | a message sent before the history has loaded stays on screen with its reply |
| W7 | Low | Long unbroken text overflows chat bubbles. Fix it in `components/Conversation.tsx`. | a long unbroken link wraps… |
| W8 | Low | "Remember that X" twice stores two notes: `memory/store.remember` doesn't deduplicate. | telling Ava the same thing twice keeps one note |
| W9 | Low | Sending "Remember that" with nothing after it saves the note "that". | "Remember that" with nothing after it… |
| W10 | Low | The console's People tab shows the raw method name `account_signed_in`. | People says in plain words… |
| W11 | Low (phone) | The offline notice says Connections is "on the right". On the dashboard it's a sidebar page. | mobile spec |
| W12 | Low (a11y) | The phone view switcher doesn't expose which view is current. The dashboard's sidebar uses `data-active`; check it's announced. | mobile spec |

Already fixed: a blank chat message used to cause a 500 (fixed by commit `7c39493` from the Mac).

To re-test on the dashboard:
- **Chat:** persistence, Enter and Shift+Enter, double-send, 4,000-character limits, script tags, network failures, quick actions.
- **Notebook:** Forget with confirmation, and 404 for an unknown or already-forgotten note.
- **Notices:** paused and offline.
- **Connections:** the guides, refusals, Disconnect, and WhatsApp shown as unavailable.
- **Access:** 401, 403 and CSRF checks on every endpoint.
- **Log-in:** `next=` when `/app/me` is slow.
- **Phone:** the current page is exposed to screen readers, and there's no sideways scroll.

## Fixed by the API test agent (`backend/tests/api/`)

These came with their fixes, in 152 test functions (457 cases):
- JSON containing a NUL character caused a 500.
- There was no limit on wrong current-password guesses at `POST /auth/password`.
- A second account trying to hire Ava was told the owner's full name.
- **High:** a quiet-hours time in non-ASCII digits ("1٠:0٠") was accepted, and then the owner brief crashed every turn for everyone. Times are now strict ASCII `HH:MM`.
- Malformed onboarding answers caused 500s or stored junk, and `"vip": "false"` made someone a VIP.
- Contacts were matched with ILIKE, so a name like "P%" overwrote Priya.
- 422 validation errors echoed submitted values, including API keys and passwords.

Still open: re-sharing a file after it was removed and its facts were read causes a 500. `ingest()` deletes the old Document while retired facts still point at it.
- Fix: before the delete, `update(MemoryItem).where(MemoryItem.document_id == old.id).values(document_id=None)`.
- Test: `backend/tests/api/test_intro_negative.py::test_a_removed_file_can_be_shared_again_once_facts_were_read_from_it`, marked xfail.

Still open: the marketplace and agent page show "Works for <first name>" to everyone. Decide whether the public should see who hired an agent.

## Needs updating for the dashboard

The browser specs were written against the earlier layout, before the dashboard. They may need:
- **Log out:** now in the account menu (nav, hire, introduction) and the sidebar's account menu (dashboard), not a standalone "Log out" button.
- **`/workspace`:** now redirects to `/dashboard/chat`. The chat heading "Hello, <name>. What shall we work on?" is unchanged.
- **Notebook, connections and the agent card:** now separate dashboard pages, not a right-hand rail.

## Product decisions waiting on you

These came up across all the agents.

- **Who can see who hired an agent:** "Works for <first name>" appears on public pages and in `GET /app/agents`.
- **Account enumeration:** a 409 on sign-up, and a lockout (429) versus "wrong password" (401), both reveal that an email has an account.
- **Lockout as denial of service:** five wrong passwords every 15 minutes keeps the owner locked out.
- **Silent fallbacks:**
  - An invalid timezone keeps the old one.
  - A relationship in lowercase becomes "other".
  - A stale step is ignored with a 200.
  - Long answers are cut without telling the person.
- **Uploads:** there's no request-size limit before the 10 MB check, so set a reverse-proxy body limit in production.
- **Admin:**
  - The token is accepted without `Bearer`.
  - The console keeps the token in `localStorage` on the same origin as the public site.
  - `/api/api/*` is reachable through the frontend proxy (the token is still required).
- **Hire consent:** `"accepted_notice": 1` counts as consent.
- **Stranger replies:** "I've sent a code to your usual channel" is sent even when no code went out (anti-probing, but untrue).
