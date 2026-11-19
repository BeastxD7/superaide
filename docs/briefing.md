# While you were away (briefing and drafted replies)

Where: Dashboard → Home, the "While you were away" card (issue #485).

When you have not written to Ava for about three hours and there is something to tell, Home opens with a short briefing:

- **Who wrote** (people Ava doesn't know), on which channel, how many messages, how long they have waited, whether they
  look urgent or asked for something risky, and what they wrote, quoted and labelled "written by someone Ava doesn't
  know. Not verified."
- **What is overdue**: promises (see `promises.md`) past their time.
- **A suggested reply for each person** (up to four are written per briefing; one model call per message, never again for
  the same message). Edit the words if you like, then **Send** or **Dismiss**. **Got it** closes the card until you have
  been away again.

## What is decided where

- Code picks who is in the briefing, in what order, and whether it shows at all. The model only proposes the words of a
  reply, from the person's quoted message, with no tools and no access to anything else.
- The suggestion is cleaned and checked: a link or an e-mail address in it is refused and a fixed sentence ("They've seen
  your message and will reply when they can.") is used instead, as it is with no model, over budget or on an error. Times,
  numbers and days the person did not say are flagged under the box.
- **Nothing is sent without your tap.** Send goes through the same approval and checks as a message you asked Ava to relay:
  the kill switch, the per-person daily cap, the recipient check and the audit log. It goes out after the fixed introduction
  ("Hi Meera, this is Ava, an AI assistant."). Tapping twice sends once. If they wrote again since the draft was made, it is
  marked stale and you are asked to reload.
- Words you type yourself may promise a time ("I'll reply in an hour"): that becomes a promise on your list. An unedited
  suggestion never does.
- **Dismiss only marks the suggestion dismissed.** Ava learns nothing from it and nothing from the stranger's words.
- The briefing is never pushed to Telegram or Slack and never put in your chat: only the card on Home shows what people wrote.

Not built yet: a scheduled briefing at a set time (the daily digest still carries counts) and tone settings for drafts (there
are none today, so drafts are neutral).
