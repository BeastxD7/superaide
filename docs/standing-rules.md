# Standing rules (#486)

The owner states a rule once; code applies it every time. Ava never writes, edits or reads the rules table.

| Kind | What the owner saves | What the model sees |
| --- | --- | --- |
| `allow_statement` | A sentence (160 characters), for example "He is in a meeting today." | That exact sentence as something it may say when asked where the owner is or whether they are free, and nothing beyond it |
| `forbid` | One topic from a fixed list (phone number, home address, location, calendar, health, family, money, employer matters) | A code-written label ("does not want you to discuss: their health"), never the owner's own words |

- Scope: `everyone` (every non-owner, including strangers on the receptionist) or `contact` (one person from the owner's people list). The owner never has rules applied to them.
- Optional end date: an expired or stopped rule stops applying on the next message.
- API: `GET/POST /app/standing-rules`, `PUT/DELETE /app/standing-rules/{id}`, owner only. Every change is audited.
- Rules only add allowed sentences and refusals. They never widen who can read a memory: that stays with the audience gate.
- The free-text `never_share` setting stays out of every non-owner prompt (`memory/knowledge/profile.py`); `forbid` rules are the enforceable replacement.
- No flag: with no rules saved nothing changes.
