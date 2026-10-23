"""The rehearsal room (#58): the owner practises a hard conversation, Ava plays the other side.

Nothing here talks to anyone but the owner: no channel, no `event` rows (so a rehearsal can't
resurface in recall), no `memory_item` writes, no tools. The only outside call is the model, under
BudgetGuard. A session is deleted a day after it starts unless the owner keeps it, and the owner
can erase it at any time.
"""
