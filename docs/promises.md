# Promises (the commitments ledger)

Where: Dashboard → Promises (issue #480).

A promise is something you or Ava told someone would happen, with a time: "I'll reply to Meera in an hour". Ava keeps
them on a list, reminds you shortly before one is due, and tells you when one is late.

## Where a promise comes from

Only from you, and always written by code, never by the model:

- **A message you approved.** You tell Ava "tell Meera I'm busy, I'll reply in an hour" and approve the card. When the
  message is sent, code reads the time from the words *you* typed (not from the text the model drafted) and adds a
  promise for Meera due in an hour. Words without a promise to reply ("I'm in a meeting at 3") or without a time add
  nothing. Approving the same card twice adds one promise.
- **The Promises page.** Add one by hand: what, to whom, by when.

What a stranger writes never creates, moves or closes a promise.

## Reminders

Each promise has at most three, each sent once:

1. **Before it is due**: at the last quarter hour at least five minutes ahead (due 15:08: 15:00); halfway for a promise
   under 15 minutes away; none under four minutes.
2. **When it is due**: the promise becomes *Overdue* (a state change, no message). Overdue promises are listed in the
   daily briefing.
3. **Half an hour later**: one more message.

They are delivered like every other note to you. In your quiet hours, or when your interruption budget (the hourly and
daily limits under Needs you settings) is used up, they stay in the app and nothing is pushed to Telegram or Slack.

## Closing

Mark it **Done** or **Cancel** it, or write to that person yourself from Settings → People you don't know (the promise is
then closed as "You replied"). Closing stops its reminders. Approving another message to the same person is not a reply
and does not close it.

## Retention

Nothing is deleted. Closing sets a state and a time; **Remove** only hides a closed promise from the list (`removed_at`).
Promises are visible only to you and never put in what Ava says to anyone else.
