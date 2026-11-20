# Friends and trust tiers

Who someone is, and what Ava may tell them, is decided in code from the database. The model never writes any of it.

| Tier | How an account gets there | What Ava does |
| --- | --- | --- |
| Stranger | Anyone not linked and not listed | The receptionist: public details only, takes a message |
| Unverified friend | The owner listed its Telegram username for a contact, or it opened an invite the owner made | Same as a stranger (public only, no name, no personal or work information) plus one fixed line, once a day, saying it is not confirmed yet. The owner is told and approves in People |
| Verified friend | The owner approved the account, or typed its immutable id for the contact | Recognised as that contact, greeted by the owner's name for them on the first reply |
| Colleague | Linked with the colleague relation | As before |

The owner approves in **People**: the card shows the platform's immutable identifier (Telegram id, Slack member id).
The username and shown name are labelled as ones the sender chose. A name or "I'm Meera" in a message never changes a
tier. Invite links (Telegram) work once, for a week, only through the connected bot; whoever opens one is an
unverified friend until approved. No setting or flag.
