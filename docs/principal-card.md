# Principal Card (#135)

Every email Ava sends is signed, by default:

```
--
Regards,
Ava, personal assistant of Ananya

Ava is an AI assistant acting for Ananya. What she may and may not agree to, and how to report a problem: https://your-address.example/c/Xk3pQ9aB
```

The first line pair is the owner's wording (Settings, Principal card; `{agent}` and `{name}` are filled in). The second paragraph and the link are fixed in code, so the message always says she is an AI. The link opens a public page that shows only the card: who she acts for, what she may and may not agree to, and until when (`PRINCIPAL_CARD_DAYS`). It reads no memory, contacts or relationships and is signed with a key derived from the vault master key.

## Who decides what

- `PRINCIPAL_CARD_ENABLED` (default true) is the global kill flag. False: no sign-off anywhere and `/p/card/*` answers 404.
- Settings has the default (on) with an off switch, the first name (blank uses the first name on the account; an initial only if the owner chose one; never a full name), the wording, and "also add it to Telegram and Slack replies to other people" (off by default; Telegram and Slack are untouched unless the owner turns this on).
- Each email's approval card shows "Sign as Ava, personal assistant of <first name>", ticked by default. Unticking sends that email in the owner's own name with no Ava line.

## How the tick is bound

When an email is queued, Gmail gets two drafts: the signed one and the plain one. The card's approval payload names the one that will be sent (`draft_id`, `content_hash`, `sign_as_ava`) and the other (`alt_draft_id`). Ticking swaps them and re-seals the approval (`bound_hash`), only while the card is waiting (`POST /app/approvals/{id}/signature`). After the yes the payload is sealed and the executor checks the draft's hash before sending, so the choice cannot be flipped. The unused draft is deleted when the email is sent or rejected.

## Counters

`GET /app/principal-card/stats`: cards shown (emails sent with the sign-off, and channel replies), distinct cards opened, cards revoked one by one and all at once. Numbers and card ids only. The sent list is the owner's own view and is revocable per card or all at once.

## Existing owners

A stored legacy `opted_in` value is ignored: the default is on unless the owner has switched it off with the new switch (`sign_default`). There is no migration; the state lives in the instance's existing key/value table.

## The link (#438)

- Every link that leaves the app (the card, verification and reset mail, channel footers, push) is built by `superaide.links.public_url()`: `PUBLIC_BASE_URL` when set, else `FRONTEND_ORIGIN`. A test fails on any new use of `frontend_origin` in link-building code (`tests/principal/api/test_card_link.py`). At startup a warning is logged when the address is still localhost on a deployment that looks reachable from outside. Settings, Principal card shows the address in use and warns if it is local.
- The card link is short: `/c/` plus 8 URL-safe characters (about 30 characters in total). It names a row in `card_link` (instance, card id, generation, expiry; no personal data; a platform lookup table exempt from Row-Level Security like `ask_handle`, because a recipient has no account). The page shows the owner's current name and lists, so editing them changes every card still valid.
- Revoking all cards (generation) or one card (its id) kills the link; so does expiry (`PRINCIPAL_CARD_DAYS`). Long signed tokens from emails sent earlier keep working until they expire.
