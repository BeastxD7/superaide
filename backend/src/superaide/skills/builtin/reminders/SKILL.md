---
name: reminders
description: Set, list, move, edit and cancel reminders and follow-ups for the owner, on the channel they pick, and bring them back at the right time.
license: Proprietary
metadata:
  publisher: superaide
---

# Reminders

When the owner asks to be reminded, or to follow up with someone, call `create_reminder` with the
task in a few words and the time in their own words ("tomorrow at 5pm", "in 2 hours", "Friday").
Confirm the exact time you set. If they didn't say when, ask.

Reminders always show in the app chat. When the owner says where they want it ("text me on
Telegram", "ping me on Slack"), pass `channel` (`telegram`, `slack`, or `app` for the app only);
otherwise leave it out and it follows their usual choice. You can send only to channels the owner
linked; the system prompt lists them. If the channel isn't linked, the tool says so: tell the
owner, say they can link it under Connections, and offer the app instead. Never say you can't
choose the channel.

You can also manage reminders that already exist:

- `list_reminders` shows their open reminders with each one's id, task and time. Use it when they
  ask what's coming up, or before changing a reminder whose id you don't have. It only reads the
  reminders this skill made, nothing else Ava knows.
- `update_reminder` moves a reminder to a new time (`when`, in their words), rewords it (`task`)
  and/or changes where it arrives (`channel`). Confirm the new time it gives back.
- `cancel_reminder` cancels one so it won't be delivered.

Ids come only from the results of `create_reminder` or `list_reminders`; never make one up. If more
than one reminder could match what the owner means, list them and ask which. Only open reminders
can be changed; one that is done or cancelled stays as it is.
