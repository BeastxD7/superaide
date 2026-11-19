# Approve by reply (#642)

The owner can answer an approval card where they are, without a model deciding anything.

- **Typed:** in the owner's own verified direct message (Slack, Telegram) or the app chat, a one-word `send`, `yes`,
  `ok`, `approve` approves, and `cancel`, `no`, `don't send` rejects, the one card Ava just announced there. With two or
  more waiting, Ava lists them with numbers and `send 2` answers the list. Nothing is ever guessed.
- **Buttons:** the notice of a card that may be approved from chat carries Approve and Cancel buttons (Telegram by
  default; Slack when `APPROVAL_BUTTONS` includes `slack` and Interactivity is on in the Slack app).
- **Where it can never happen:** a channel or thread, a message with a file, a quote or extra words, someone other than
  the confirmed owner, a card asked for after Ava read other people's words (inbox, mail, web, files) or on a call, an
  expired or changed card, a card from another conversation, a kind not in `APPROVE_BY_REPLY_KINDS` (default
  `relay_message,gmail_send`), or while Ava is paused. Those say to use the app. A "no" is always accepted.
- **How:** `approvals/shortcut.py` reads the word in the pipeline before any model runs and calls the same
  `policy.actions.resolve` as the app, so the burst brake, kill switch, content binding and the executor's own checks
  all run. The reply is written from the executor's result. A card records the conversation that asked for it inside
  its seal (`approvals/origin.py`); the reply that announced it records the card's id, which is what "send" binds to.
- **Off:** `APPROVE_BY_REPLY_KINDS=` (empty) and `APPROVAL_BUTTONS=` (empty).
