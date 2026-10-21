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

## Fixed by the API test agent (`backend/tests/api/`)

These came with their fixes:
- JSON containing a NUL character caused a 500.
- There was no limit on wrong current-password guesses at `POST /auth/password`.
- A second account trying to hire Ava was told the owner's name.
- Quiet hours weren't validated strictly, and malformed onboarding answers weren't read safely.

Still open: the marketplace and agent page show "Works for <first name>" to everyone. Decide whether the public should see who hired an agent.

## Needs updating for the dashboard

The browser specs were written against the earlier layout, before the dashboard. They may need:
- **Log out:** now in the account menu (nav, hire, introduction) and the sidebar's account menu (dashboard), not a standalone "Log out" button.
- **`/workspace`:** now redirects to `/dashboard/chat`. The chat heading "Hello, <name>. What shall we work on?" is unchanged.
- **Notebook, connections and the agent card:** now separate dashboard pages, not a right-hand rail.
