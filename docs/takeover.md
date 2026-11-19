# Stepping into a conversation (live takeover)

Where: Settings → People you don't know → Step into a conversation (issue #493).

When you write to someone Ava doesn't know, from that panel, Ava steps out of that conversation:

- **Ava does not reply there.** Code decides this, the model is not asked and no model call is spent. What the person
  writes meanwhile is stored and shown to you; nothing else happens to it.
- **Ava still reads what you wrote.** When she takes the conversation back, your messages are in her history, marked as
  yours (not hers). They carry the conversation's audience only, so they never reach another conversation.
- **Your words are labelled.** The person receives "Message from <you or 'the person I work for'> (not Ava): ...". Your
  first name is used only if you allowed it under "Use my first name".
- **You see it.** The panel says "You're handling this. Ava isn't replying here." and when she will take it back.

Ava takes the conversation back:

1. when you press **Give control back to Ava**;
2. when you tell her, in your own chat: "give control back to Ava" (or "...for Meera" when you handle several);
3. by herself after **150 minutes** without a message from you (change it in the same panel, 5 to 1440 minutes, or for
   the whole server with `TAKEOVER_IDLE_MINUTES`; 0 turns takeover off). Each message you send restarts the window. The
   window is checked whenever the conversation is read and by the `takeover_expire` job, so a stopped worker cannot keep
   her silent.

Nothing the other person types can hand the conversation back. A kill switch (pause) stops sending from the panel too.
If the channel is not connected, the message is saved, you are told, and Ava still stays out until you hand it back.
