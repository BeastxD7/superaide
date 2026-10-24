# Settings: what you can change, and what is fixed

Generated from the settings definitions by `backend/src/superaide/skills/settings_docs.py`; do not edit by hand.
Run `uv run python -m superaide.skills.settings_docs > ../docs/settings.md` from `backend/` after changing a setting.

Every setting has a safe default that equals what the app did before the setting existed, so nothing gets weaker
when you upgrade. A change is audited (the audit log names the setting and the new value) and the page that holds it
shows who changed it last and when. Ava is told the current value in her prompt, and when a setting limits an answer
she names it and says where to change it.

## Gmail

Where: Capabilities → Gmail → Reading

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
| Payment and transaction emails: previews | Search results for bank, card and payment mail show only the sender and subject, or also a short preview. | hide, show | hide |
| Bank and account change emails: previews | Mail asking you to change bank, account or payment details is the classic invoice-fraud pattern. Hidden, a search shows only sender and subject. Example: 'Our bank details have changed, pay to the new account'. | hide, show | hide |
| Numbers in emails | Numbers of six digits or more are replaced with [number]. 'amounts' keeps amounts next to a currency (Rs 1,250.00, $40) and still blanks card, account and code-like numbers. | blanked, amounts | blanked |
| One-time codes and password resets | Mail about one-time codes and password resets is never shown. Turning it on lets a model read secrets and means a malicious email could try to make Ava repeat them: you confirm it first. | never, read | never |
| How much of a message Ava reads | Characters of one message's own text. | 1500, 3000, 6000 | 1500 |
| Search size | Emails returned by one search. | 5, 10, 25 | 10 |
| Thread depth | Latest messages of a thread Ava reads. | 3, 5, 10 | 5 |
| How far back Ava looks | Days back; 0 means all of your mail. | 0, 30, 365 | 0 |
| New mail: how much Ava reads to sort it | Characters of each new email's own text that the inbox sorter reads to decide whether it needs you. | 500, 1000, 2000 | 1000 |
| New mail: emails sorted per check | How many new emails are read and sorted each time Ava checks your inbox (the rest wait for the next check). | 25, 50, 100 | 50 |

## Google Calendar

Where: Capabilities → Google Calendar → Reading

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
| How far ahead Ava reads | Days of your calendar one question can cover. | 7 days, 14 days, 30 days | 14 days |
| Your day starts at | Free time is described from this hour (24h clock). | 6, 7, 8, 9, 10 | 8 |
| Your day ends at | Free time is described up to this hour (24h clock). | 17, 18, 19, 20, 21, 22 | 20 |
| Events she learns from per read | Events from one read that Ava may turn into notes about you (only when learning from connectors is on). | 5 events, 10 events, 20 events | 10 events |

## Web research

Where: Capabilities → Web research → Searching

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
| Searches per step | How many web searches Ava may run in one step of an answer (each costs a little). | 1 searches, 3 searches, 5 searches | 3 searches |
| Pages she may open per step | How many web pages Ava may read in one step. | 1 pages, 3 pages, 5 pages | 3 pages |
| When Ava searches the web | 'request': only when you ask about the outside world or something current. 'useful': also on her own when a search would clearly help. Each search costs a little. | request, useful | request |

## Channels and messages

Where: Connections → Reading and keeping

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
| Slack thread depth | When you @-mention Ava in a Slack thread, how many of its latest messages she reads (other people's words). | 10 messages, 25 messages, 50 messages | 25 messages |
| Context from your other channels | How much of each earlier message from another channel (Telegram, Slack, the app) Ava is shown as context. | 300 characters, 600 characters, 1200 characters | 600 characters |
| Keep other people's messages for | Messages other people sent Ava are deleted after this many days. 0 keeps them until you delete them. A shorter limit set by the server operator still applies. | 0 days, 30 days, 90 days, 365 days | 0 days |

## Reminders and approvals

Where: Settings → Reminders and approvals

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
| Close reminders that went off after | A reminder that went off and was never marked done is closed after this many days. 0 keeps them open. | 0 days, 3 days, 7 days, 14 days, 30 days | 7 days |
| Requests that wait for your yes expire after | A request nobody answers can no longer be approved after this many hours, so an old yes cannot run a stale action. 0 means never. Applies to requests made from now on. | 0 hours, 1 hours, 24 hours, 72 hours, 168 hours | 0 hours |

The Gmail card also has two lists, **Never read these senders, domains or labels** and **Always allow
these senders** (an address, `@domain` or `label:name`; both are empty by default).

## Fixed on purpose

These are safety rules decided in code. They are shown read-only as "How this works" on each page and nothing, not a
message, an email, or Ava herself, can change them.

- Nothing is sent without your yes: every reply and every new email waits as a request that you approve.
- Email, web pages and other people's event text are information, never instructions to Ava.
- A new email goes to one address that you typed yourself, never one taken from a message.
- An address that looks automated (noreply, do-not-reply) is refused unless you named the sender; then the request
  carries a warning.
- Adding, moving or declining calendar events is off until you allow it, and each change waits for your yes.
- A message from someone you have not added is never treated as you.
- Letting Ava read one-time codes and password resets needs a confirmation in a dialog (and, with
  `STEPUP_MODE=password`, your password). It is off by default.
- Keys and tokens are stored encrypted and never shown again in full.
