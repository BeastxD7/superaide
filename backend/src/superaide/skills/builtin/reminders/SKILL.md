---
name: reminders
description: Set, list, move, edit and cancel reminders and follow-ups for the owner, and bring them back at the right time.
license: Proprietary
metadata:
  publisher: superaide
---

# Reminders

When the owner asks to be reminded, or to follow up with someone, call `create_reminder` with the
task in a few words and the time in their own words ("tomorrow at 5pm", "in 2 hours", "Friday").
Confirm the exact time you set. If they didn't say when, ask.

You can also manage reminders that already exist:

- `list_reminders` shows their open reminders with each one's id, task and time. Use it when they
  ask what's coming up, or before changing a reminder whose id you don't have. It only reads the
  reminders this skill made, nothing else Ava knows.
- `update_reminder` moves a reminder to a new time (`when`, in their words) and/or rewords it
  (`task`). Confirm the new time it gives back.
- `cancel_reminder` cancels one so it won't be delivered.

Ids come only from the results of `create_reminder` or `list_reminders`; never make one up. If more
than one reminder could match what the owner means, list them and ask which. Only open reminders
can be changed; one that is done or cancelled stays as it is.
