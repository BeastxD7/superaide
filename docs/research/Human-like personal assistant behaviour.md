# What a thoughtful human assistant does, and what Ava still lacks

Written 25 Oct 2026. Research only: nothing here is built. It starts from a real test (issue [#472](https://github.com/BeastxD7/superaide/issues/472)), turns the owner's expectations into requirements, compares Ava with how good human assistants and the best current systems behave, and ends with design principles, a reference architecture, worked scenarios and an evaluation plan. The work is split into issues listed in the tracking issue [#488](https://github.com/BeastxD7/superaide/issues/488): #474 to #487 (cap and store-first #474, honest greeting #475, owner inbox #476, notifications #477, proxy messaging #478, friends #479, commitments #480, concurrency #481, conversation quality #482, own mail #483, evaluation #484, briefings #485, standing policy #486, abuse limits #487).

Names in the story are fictional: the owner is Ananya, the friend is Meera.

## Contents

1. [The story, and why it matters](#story)
2. [Requirements from the owner](#requirements)
3. [What humans do well as assistants](#humans)
4. [What Ava does today, checked against the code](#today)
5. [Prior art and what to take from it](#prior-art)
6. [The gap list](#gaps)
7. [Design principles](#principles)
8. [Reference architecture](#architecture)
9. [Worked scenarios, the Meera story replayed](#scenarios)
10. [Evaluation plan](#eval)
11. [Build order, effort and owner decisions](#order)
12. [Sources](#sources)

<a id="story"></a>
## 1. The story, and why it matters

Ananya linked Slack and Telegram, tested shared memory (it worked: Ava recalled the app chat and Slack from Telegram), then gave the Telegram bot to a friend, Meera: "if I'm not available, talk to this bot." In ten minutes:

- Meera got "Hi, I'm Ava, an AI assistant", then "I don't keep memories between conversations" when she asked "did you forget about me?", then four evasive answers to "whom do you work for?".
- She gave her name, email and topic. Two replies ended with "I've sent a code there: type it here to link this account", about a code that did not exist.
- Her last message, "I need it as soon as possible, maybe within 5 minutes", got "I've passed on what you sent. I can't keep chatting just now." After that, silence. That message was never stored.
- Meanwhile Ananya asked his own Ava three times "is anyone asking about me?" and was told no. A real line then appeared in his chat ("Message taken from someone new on Telegram...") and Ava called it a mistake.

A human receptionist who behaved this way would be fired for any one of these: not saying whom she works for, hanging up on an urgent caller after "I've passed it on" without writing it down, and telling her boss that nobody had called while the caller was on hold. The product promise is "when I'm busy, people can talk to me instead", so this is the core path, not an edge case.

<a id="requirements"></a>
## 2. Requirements from the owner

These are treated as acceptance criteria for the whole effort.

| # | Requirement | Main issue |
|---|---|---|
| R1 | A message cap, if any, is told up front ("you can send up to N messages here; after that I'll stop replying but I'll still pass your messages on"), warned about when near, and never ends in silence | expectation setting |
| R2 | Promise only what Ava can do. Use what the owner allowed in onboarding (name, work). Never say "I can answer from public information" with none to give | honest greeting |
| R3 | Ava knows what is happening on all channels, now: "is anyone asking about me?" is answered from facts | owner inbox |
| R4 | Real notifications: who, what, how urgent | notifications |
| R5 | The owner types "tell Meera I'm busy, I'll reply in an hour" in the app, and Ava messages Meera on Telegram, with a safe authority model and an audit trail | proxy messaging |
| R6 | The long ambition: behave like a thoughtful human assistant (expectations, remembering people, commitments, who is who, follow-up, tact, honesty about limits, urgency, juggling several conversations), and beat the assistants of this era | whole set |

<a id="humans"></a>
## 3. What humans do well as assistants

This is a synthesis from the assistant profession, conversation analysis and the project's own earlier review ([Humanistic assistant architecture review](Humanistic%20assistant%20architecture%20review.md) covers how trust is earned in rungs). Ten behaviours matter here.

1. **They set expectations at the start.** "He's in a meeting until 3. I can take a message and he usually replies within the hour." The caller never has to guess what happens next. This is the grounding work of conversation: each side shows what it has understood and what comes next ([Clark and Brennan 1991](#s-clark)).
2. **They know who is on the line.** Name, relationship, last conversation, and whether this person gets through. A friend is greeted as a friend; an unknown caller gets politeness and a notepad.
3. **They never promise what they cannot do**, and when they can't, they say what they can do instead. Violating this is the usual cause of lost trust (the "Maxim of Quality" in [Grice 1975](#s-grice)).
4. **They write it down before they say they will.** A message is taken first, then acknowledged.
5. **They tell the boss, with the right amount of noise.** An urgent call from a friend interrupts a meeting with a note; a routine one waits for the end-of-day list. The note says who, what, by when and what is suggested.
6. **They track promises.** "I told Meera he would reply within five minutes" becomes a commitment with a clock. Obligations are a first-class object in assistant work ([commitments ledger idea in the project's own cross-channel design](Cross%20channel%20multi%20party%20memory%20design.md), Layer 4).
7. **They speak for the boss on instruction.** "Tell her I'm busy" is carried out in the boss's voice, and the boss can see afterwards what was said. The instruction is the authority.
8. **They keep several conversations apart.** Different people, different promises, no leakage between them. This is contextual integrity in practice: information flows only along the norms of the context it came from ([Nissenbaum, Privacy in Context, 2010](#s-nissenbaum)).
9. **They repair.** When someone is confused ("did you forget about me?"), they notice, correct the false assumption, and move on ([conversational repair, Schegloff, Jefferson and Sacks 1977](#s-repair)).
10. **They end gracefully.** "I'll pass this on and he'll come back to you" closes the loop, and later they follow up if nothing happened.

<a id="today"></a>
## 4. What Ava does today, checked against the code

Verified on `main` at commit 59bcade0. Paths are under `backend/src/superaide/`.

### 4.1 The findings of #472, confirmed

| Finding | Where | Detail |
|---|---|---|
| Message lost at the cap, while Ava says it was passed on | `reception/service.py:123-126`, `:44` | The cap check runs before `_talk` and `store_message` (`:130-136`). `LIMIT_REPLY` says "I've passed on what you sent" but nothing is stored. Within the hour after the first limit reply, `_said_recently` returns `""`: total silence (`:126`, `pipeline.py:259`). |
| The owner's Ava cannot see taken messages | `brain/loop.py:98` and the owner tool set | No tool reads `ReceptionMessage` (`reception/models.py:42-63`). `recall` is memory-only. So "is anyone asking?" can only answer no. |
| The notification is minimal and per all senders | `reception/service.py:291-303`, `:47` | One fixed line, suppressed if any message was taken in the last 15 minutes, from anyone. No name, summary, deadline or link. With triage on, `_to_triage` (`:231-265`) creates a "Needs you" item instead, which still has no sender-aware push for a HOLD decision. |
| "Whom do you work for?" unanswerable | `reception/models.py:34`, `reception/turn.py:78-95`, `brain/receptionist.md:1-14` | `show_first_name` defaults to false and `public_profile` to empty. The prompt then says "(None. You can only take a message.)" but the persona's first line still says "answer from the public information below". |
| Misleading claim note | `reception/service.py:40-43, 120-122, 139-140`, `identity/linking.py:30-43` | `CLAIM_NOTE` is appended whenever `detect_claimed_name` matches, on purpose, so nobody can probe who Ava knows. The result is a false sentence for a real person who is simply introducing herself. |
| No notion of a friend | `identity/resolver.py` (people are resolved by platform id), `reception/service.py` | Anyone not linked is a stranger with the same script. |
| Own mail flagged as suspicious | owner chat | The owner's Ava does not see her own sent mail as hers (a separate action-ledger problem, [see issue 10](#order)). |

### 4.2 More findings from this analysis

- **A taken message exists only if the model chooses to fill `take_message`.** `service.py:133-136` stores nothing when `taken` is `None` and mode is `model`. A reply of "I've passed that on" written by the model, with `take_message: null`, loses the message with no log. The persona only says to fill it "once you have the gist" (`receptionist.md:10`). Storage must be code-driven: every inbound message is stored; the model only adds structure.
- **Onboarding's "what am I allowed to tell them" does not reach the receptionist in any usable form.** The `share` step (`app/onboarding.py:454-466`) writes `OwnerPreference.shareable`, a list of enum values such as `company_role` and `office_city`. The receptionist reads only the labels (`reception/turn.py:86-89`) and tells the model "kinds of things you may share, but only if they appear above", yet the values are never written anywhere above: nothing in onboarding writes `ReceptionSettings.show_first_name`, `public_profile`, or a public memory. So the owner says "you may say my name, company and role", and Ava has a permission with no data. The owner's name (`Person.display_name`) and the company and role facts exist, but no code joins them. The same list is also never asked as "may I say your name?", because name is not in `SHARE_OPTIONS` (`onboarding.py:60-66`).
- **The persona itself causes the "I don't keep memories" lie.** `receptionist.md:8` says "You can't ... remember anything for later", and `HISTORY = 12` turns (`turn.py:38`) *are* remembered within the chat. The model resolves the contradiction by denying memory.
- **`CLAIM_RE` is far too broad** (`identity/linking.py:30-33`): "I'm / I am / it's / this is" followed by any word. "I'm in a hurry", "this is urgent" and "it's about the project" all become "claimed names" ("in a", "urgent"), run `_claim` and `claims.review`, and get `CLAIM_NOTE`. Zeeshan's single reply with "zeeshan is my name" is just one of many triggers.
- **Caps count every inbound event, including from a conversation the owner would call one request** (`service.py:66-81`). There is no up-front statement of the cap, no warning near it, and no resume message: the reset is silent.
- **The 15-minute note gate counts all senders** (`service.py:295-299`). A second person writing within 15 minutes of the first is never announced.
- **Digest lines are counts only** (`service.py:306-319`): "3 from people I don't know". The owner cannot learn who.
- **The owner's model never sees stranger text, deliberately** (`service.py:16-19`). That is the right rule for injection safety, but it means a structured, code-rendered summary is the only safe bridge; today even that bridge does not exist.
- **Notification as a chat line is not a sourced event.** `proactive/notify.py:~146` logs it as an outgoing event with `meta.kind`; the owner's next turn replays it as an assistant message with no tool trace (`brain/turn.py:81`, `tools_used`), and the model, seeing no tool call for it, disowned it. The fix is to give such lines a structured, citable source, not to loosen the claim checker (`brain/verify.py`).

### 4.3 What works and must not regress

Cross-channel memory for the owner (ADR [0008](../decisions/0008-one-ava-across-channels.md)); the audience gate in SQL (`context/audience.py`); stranger turns having no tools and no memory writes; triage and escalation for known senders (ADR [0010](../decisions/0010-triage-and-escalation.md)); the spend cap.

<a id="prior-art"></a>
## 5. Prior art and what to take from it

Sources were fetched on 25 Oct 2026 unless marked "not re-fetched". Claims about products are limited to what the sources say.

### Memory and long-lived agents
- **Generative Agents** ([Park et al., arXiv 2304.03442](https://arxiv.org/abs/2304.03442), Apr 2023): agents store experiences, reflect and plan; retrieval scores recency, importance and relevance. *Take:* a per-person "relationship summary" produced by reflection, retrieved by relevance, not "newest N".
- **MemGPT / Letta** ([Packer et al., arXiv 2310.08560](https://arxiv.org/abs/2310.08560), Oct 2023): memory tiers with the model paging its own memory; Letta adds labelled memory blocks and a background "sleep-time" agent that edits memory ([Letta blog](https://letta.com/blog/memgpt-and-letta)). *Take:* a small always-in-context block per active conversation ("who this is, what I promised"), maintained by a background job. *Do not take:* the model editing identity or trust.
- **Zep / Graphiti** ([Rasmussen et al., arXiv 2501.13956](https://arxiv.org/abs/2501.13956), Jan 2025): a temporal knowledge graph with validity intervals; reports gains on LongMemEval. *Take:* facts about a person have a valid-from/valid-to ("busy until 3 pm"); this is already tracked in #414. No need for a graph store.
- **Mem0** ([Chhikara et al., arXiv 2504.19413](https://arxiv.org/abs/2504.19413), Apr 2025): extract, merge, retrieve key facts, with a graph variant; reports better LoCoMo results at lower token cost. *Take:* extract-then-merge for per-person notes. Vendor-reported numbers, treat as indicative.
- **LoCoMo** ([Maharana et al., arXiv 2402.17753](https://arxiv.org/abs/2402.17753), Feb 2024) and **LongMemEval** ([Wu et al., arXiv 2410.10813](https://arxiv.org/abs/2410.10813), ICLR 2025): benchmarks that include temporal reasoning, knowledge updates and **abstention** (saying "I don't know"). *Take:* abstention and "what did I promise X" are testable; our scenarios borrow the question types.

### Proactivity, theory of mind, calibration
- **Proactive Agent / ProactiveBench** ([Lu et al., arXiv 2410.12361](https://arxiv.org/abs/2410.12361), Oct 2024): predicts when to offer help unprompted; best fine-tuned model reached 66.47% F1, so false alarms and misses are common. *Take:* proactivity needs a cost model (interrupt budget) and the owner's approve/reject signal; ADR 0010 already has the budget.
- **FANToM** ([Kim et al., arXiv 2310.15421](https://arxiv.org/abs/2310.15421), Oct 2023): models fail at tracking *who knows what* in multi-party conversations, even with chain of thought. *Take:* never rely on the model to remember who was told what; keep an explicit "what each person has been told" ledger in code (the project's epistemic ledger, Layer 4 of the cross-channel design).
- **Language Models (Mostly) Know What They Know** ([Kadavath et al., arXiv 2207.05221](https://arxiv.org/abs/2207.05221), Jul 2022): models can estimate whether they know an answer. *Take:* abstention is possible, but the capability list Ava states should come from the code (what tools exist), not from the model's self-assessment. This is the "capability honesty" principle of #328.

### Contextual integrity and multi-party
- **Nissenbaum, Privacy in Context** (Stanford University Press, 2010; not re-fetched): a flow is appropriate if it matches the norms of its context (sender, receiver, subject, attribute, transmission principle). Recent LLM work applies it and warns the application is often loose ([arXiv 2501.19173](https://arxiv.org/pdf/2501.19173), [arXiv 2506.04245](https://arxiv.org/html/2506.04245v4)). *Take:* the audience gate is a CI engine already; extend it to "what has Meera been told" and "what may the owner see about Meera".
- **Conversational repair** (Schegloff, Jefferson and Sacks, Language 1977; not re-fetched) and **grounding** (Clark and Brennan 1991; not re-fetched). *Take:* a repair move is a first-class eval item: after a confused or hostile message, Ava corrects the specific misunderstanding.

### Products and what they teach
- **Google Call Screen** ([Vodafone explainer](https://www.vodafone.co.uk/blog/pixel-call-screening), [TechRadar](https://www.techradar.com/phones/android/your-pixel-phone-can-now-screen-calls-to-ask-if-theyre-urgent-heres-how-it-works), 2018 and 2023): the assistant asks the caller who and why, transcribes live, shows the transcript to the owner, offers one-tap replies ("Is it urgent?", "I'll call you back"), is contact-aware, and saves the transcript. *Take:* this is almost exactly the receptionist loop; the owner sees the live transcript and taps a reply. Our version adds the proxy reply and the inbox.
- **x.ai (Amy and Andrew)** ([TechCrunch 2015](https://techcrunch.com/?p=1005382), [TWIML 2017](https://twimlai.com/podcast/twimlai/the-limitations-of-human-in-the-loop-ai)): an email scheduling assistant with a named persona. The search found no source for its later history, so no lesson about its end is drawn here. *Take:* the human-style persona set high expectations; Ava's greeting must therefore promise only what she can do.
- **Apple Intelligence notification summaries** ([T3](https://t3.com/tech/apps/apple-intelligence-news-notification-summaries-have-been-suspended), Jan 2025): paused for news apps after a complaint about a false headline; Apple said they would return with an error warning and per-app off switch. *Take:* summaries of other people's words must be faithful and labelled; for urgent items show the original text next to the summary.
- **Klarna** ([Tech.co](https://tech.co/news/klarna-reverses-ai-overhaul), 2025, secondary reporting of an interview): reversed part of an AI-first support push; wants customers to always be able to reach a human. *Take:* the human (here the owner) must always be reachable; a dead bot is the worst case.
- **Lindy, Reclaim, Superhuman AI, Microsoft Copilot:** not researched in depth in this pass. The design below uses only the sourced patterns above; a competitor teardown is listed as a follow-up in the evaluation issue.
- **Rate-limit communication:** HTTP 429 with `Retry-After` is the standard way to say "slow down, and here is when" ([RFC 6585](https://www.rfc-editor.org/rfc/rfc6585), not re-fetched). *Take:* the human equivalent is stating the limit at the start, warning at 80%, and giving the resume time, and never silently dropping.

### Agent protocols
MCP and A2A are covered in [AI era protocols and marketplace research](AI%20era%20protocols%20and%20marketplace%20research.md). For this effort the lesson is narrow: an agent-to-agent message should carry a task id, a sender identity and a status, so a proxy message and its reply can be tracked as one thread.

<a id="gaps"></a>
## 6. The gap list

| # | Gap | Kind | Human behaviour missed |
|---|---|---|---|
| G1 | Cap not told up front, no warning, silence after, message lost | bug | 1, 4, 10 |
| G2 | Greeting promises public info she lacks; onboarding choices never applied | bug and gap | 3, 2 |
| G3 | The owner cannot ask what happened on other channels | gap | 5 |
| G4 | Notifications carry no who/what, one gate for all senders, disowned | bug | 5 |
| G5 | No proxy messaging from the owner chat | new capability | 7 |
| G6 | No friends or relationship-aware treatment | new capability | 2 |
| G7 | No promises ledger, no follow-up | new capability | 6, 10 |
| G8 | No multi-conversation view, priorities | new capability | 8 |
| G9 | Conversation quality: robotic refusals, false "no memory", no repair | gap | 9 |
| G10 | Own sent mail not recognised | bug | 5 |
| G11 | No human-likeness eval, nothing gating regressions | gap | all |
| G12 | No "while you were away" briefing, no drafted replies for approval | new capability | 5, 10 |
| G13 | No standing instructions ("if anyone asks, I'm in a meeting") | new capability | 7 |
| G14 | Abuse and spam handling that never loses a message | gap | 4, 8 |

<a id="principles"></a>
## 7. Design principles

1. **Store first, speak second.** Code writes every inbound message before any reply. Limits control replies, never storage.
2. **Capabilities come from code.** The greeting, the cap statement and the "I can" list are rendered from the actual settings and tools, not written by the model.
3. **Owner words are authority; stranger words are data.** "Tell Meera I'm busy" from the signed-in owner is an instruction. What Meera wrote is a quoted, structured record, never pasted raw into a model that holds private memory (the existing rule, `service.py:16-19`).
4. **The owner reads through code-rendered views.** Tools return typed rows with sender label, channel, time and a short model-free excerpt (cut by code), plus the full text on request in a clearly marked "quoted, from outside" block.
5. **One event model for everything Ava says to the owner.** Notifications are structured events with a source id, so the claim checker can see them and the model can cite them.
6. **Honest about limits, and about time.** Say the cap, say when it resets, say "I can't tell you whether he has seen this".
7. **Relationship before script.** Who the person is (stranger, friend, colleague) selects the behaviour; the model supplies words, not trust.
8. **Promises are objects.** Anything Ava says she or the owner will do creates a commitment row with owner, counterparty, due time and state.
9. **Interrupt by budget.** Urgency is a claim; the owner's interrupt budget, quiet hours and always-through list decide the noise (ADR 0010).
10. **Everything is replayable offline.** Each behaviour has a scripted scenario with the fake model.

<a id="architecture"></a>
## 8. Reference architecture sketch

```
inbound (Telegram/Slack/app)
  -> verify -> resolve identity -> ConversationRegistry (one row per person+channel)
  -> store raw event + ReceptionMessage (CODE, always)
  -> Relationship (stranger | contact | colleague | owner)  [DB, never the model]
  -> ReplyPolicy (cap state, tone rules, standing instructions)   [code]
  -> receptionist turn (model: wording + structured take_message)  [optional]
  -> CapabilityPreamble (code: who I work for, what I can do, the cap)
  -> send; update CommitmentLedger if a promise was made
  -> NotificationEvent (structured, per sender/urgent) -> owner channel by prefs
owner chat tools (typed, read):  inbox(), conversation(id), commitments()
owner chat tools (write, gated): relay_message(conversation, text) -> confirm -> send -> audit
```

New tables (all with `tenant_id`, `agent_id`, RLS per ADR 0009):
- `contact` (person or channel identity, relationship, name, greeting rule, limits, notes, forward-urgent flag).
- `conversation` (identity, channel, status, last_seen, unread, urgency, owner_state).
- `commitment` (debtor, creditor, text, due_at, state, source_event, escalation_at).
- `relay` (owner_instruction_event, target conversation, drafted text, state: drafted, confirmed, sent, delivered, replied, failed; audit hash).
- `standing_rule` (scope, trigger, instruction, enabled, created_by_owner_event).
- `notification` (event id, sender label, summary, urgency, deadline, channel, delivered_at, dedupe key).
Existing: `ReceptionMessage`, `ReceptionSettings`, `Escalation`, `TriageDecision` are extended, not replaced.

<a id="scenarios"></a>
## 9. Worked scenarios, the Meera story replayed ideally

**S1. First contact.**
Meera: "hey". Ava (greeting rendered by code): "Hi, I'm Ava, Ananya's AI assistant. Ananya isn't available right now. I can take a message for him and make sure he sees it, and I can tell you his work and where he's based, which he's allowed me to share. You can send up to 20 messages here; after that I'll pause replying, but I'll still pass everything on." If Meera is a known friend: "Hi Meera, good to hear from you. Ananya's tied up; I'll make sure he knows you're writing."

**S2. Whom do you work for.** "I work for Ananya Menon, he works at Larkfield as a designer." If the owner allowed only the first name, she says that and nothing more; she never says "I can't tell you who I work for".

**S3. The urgent message.** Meera: "I need it within 5 minutes." Code stores it; the model extracts deadline "5 minutes" and urgency high; a notification goes to Ananya on his preferred channel: "Meera (Telegram, friend) wrote at 14:02: needs your reply within 5 minutes about the project. [Open] [Reply with a draft]". Ava to Meera: "I've passed that on and marked it urgent. I can't promise when he'll reply, but I'll tell him again if it's still waiting in a few minutes." A commitment "re-notify Ananya at 14:07 if not seen" is created.

**S4. Near the cap.** At 80% of the cap: "A heads-up: you have 2 messages left here before I pause replying. Everything you send is still passed on." After the cap: one message, "I've reached my limit for replies here for now. I'm still passing everything on to Ananya. I'll be able to reply again at 15:10." At 15:10, if she wrote after the cap: "I'm able to reply again. Ananya has seen your earlier messages / has not yet replied." Never silence, never "passed on" for something not stored.

**S5. The owner asks.** Ananya in the app: "is anyone asking about me?" The inbox tool returns one open conversation (Meera, Telegram, 14:02, urgent). Ava: "Yes, Meera is waiting on Telegram. She wrote 12 minutes ago that she needs a reply about the project within 5 minutes. Do you want me to tell her something?"

**S6. Proxy message.** Ananya: "tell Meera I'm busy, I'll reply in an hour." Ava resolves "Meera" to one open conversation; if two match, she asks which. She drafts in his voice: "Hi Meera, this is Ava. Ananya is busy right now and will reply within an hour." The owner typed an explicit instruction naming the recipient and the gist, so the draft is sent without a second tap (the text adds only what he said; a change of meaning requires confirmation). A relay row records the instruction event, text, time and delivery status. A commitment "Ananya replies to Meera by 15:15" is created and the owner is reminded at 15:00. When Meera answers, it appears in the inbox under the same conversation.

**S7. A false claim.** Meera writes "I'm in a hurry". No code is sent and no linking note appears: only a real claimed name that matches a known person triggers the linking flow, and Ava's neutral reply keeps the anti-probing property by never confirming either way.

**S8. The owner's own mail.** The owner asks about test emails; Ava recognises them in the action ledger: "Those were sent through your approvals on 24 Oct; here is the approval."

## 10. Evaluation plan

### 10.1 Offline scenario evals
The existing harness (`evals/scenario.py`, `evals/harness.py`) already has `receptionist`, `reception_profile`, `reception_show_name`, `reception_limits` and `who: stranger|contact|vip`. Add scenario files under `backend/evals/scenarios/`:
- `reception_meera.yaml`: the full story, 15 inbound messages, with the fake model; assertions: all 15 stored; the cap sentence appears in the first reply; a warning appears at 80%; the reply after the cap is truthful; a notification fires for the deadline message; the owner's inbox tool lists the conversation; "nobody is asking" never appears.
- 20 more scenarios across the gaps: empty profile, name allowed, friend vs stranger, two senders at once, proxy to the wrong person, proxy with an injection in Meera's text, quiet hours, resume after the cap, claim with and without a match (the response text must be identical: anti-probing), model returning no `take_message`, provider outage.
- Checks to add in `evals/checks.py`: `all_inbound_stored`, `no_false_passed_on`, `cap_stated`, `owner_told`, `no_stranger_text_in_owner_prompt`, `commitment_created`, `relay_audited`.

### 10.2 Human-likeness rubric
Scored 0-2 per item by a rubric run with a stronger model as judge on real transcripts and by humans on a sample, never gating alone:
1. Expectation set in the first reply. 2. Truthful about what happens next. 3. Uses the person's name and relationship correctly. 4. Answers what it may answer, refuses without stonewalling. 5. No invented facts. 6. Handles urgency (acknowledges, notifies, no false promise). 7. Repairs misunderstanding. 8. Consistent within the conversation. 9. Warm and brief, no repetition. 10. Ends the exchange cleanly. Deterministic checks gate CI; the judged rubric reports a trend.

### 10.3 Metrics
Messages lost (target 0), time from message to owner notification (p95), false alarm rate for notifications (owner-marked), cap-related silent turns (target 0), commitments met on time, proxy messages sent without edit versus edited.

<a id="order"></a>
## 11. Build order, effort and owner decisions

**Order.** (1) cap and store-first fixes and honest limits (S, bug, p1); (2) capability-honest greeting from onboarding (S to M, bug); (3) owner inbox tool and page (M); (4) notifications (M); then (5) friends and contacts (M), (6) proxy messaging (L, needs a decision on authority), (7) commitments ledger (M to L), (8) the evaluation suite alongside (1) to (4) (M, start early because it protects everything), (9) standing instructions (M), (10) briefings and drafted replies (M), (11) concurrency view (M), (12) conversation quality and repair (M), (13) own-mail recognition (S), (14) abuse limits (M).

**Owner decisions needed** are in the report that accompanied this document: how much Ava may send on her own for a proxy message; whether she may say the owner's name by default; the cap numbers and what a friend gets; which channel notifications use first and what counts as urgent; and whether "I've sent a code" may ever be softened given the anti-probing rule.

<a id="sources"></a>
## 12. Sources

Fetched on 25 Oct 2026: arXiv 2304.03442, 2310.08560, 2501.13956, 2504.19413, 2402.17753, 2410.10813, 2410.12361, 2310.15421, 2207.05221 (abstract pages). Web search results (summaries, not full articles): Google Call Screen (Vodafone, TechRadar), Apple Intelligence summaries (T3), Klarna (Tech.co), x.ai (TechCrunch, TWIML), Letta (letta.com), contextual-integrity papers (arXiv 2501.19173, 2506.04245). Not re-fetched, cited from knowledge and marked in the text: Nissenbaum 2010; Schegloff, Jefferson and Sacks 1977; Clark and Brennan 1991; Grice 1975; RFC 6585.

<a id="s-nissenbaum"></a><a id="s-repair"></a><a id="s-clark"></a><a id="s-grice"></a>
Reference list for the classics: H. Nissenbaum, *Privacy in Context* (Stanford University Press, 2010). E. Schegloff, G. Jefferson, H. Sacks, "The preference for self-correction in the organization of repair in conversation", *Language* 53(2), 1977. H. Clark, S. Brennan, "Grounding in communication", in *Perspectives on Socially Shared Cognition*, APA, 1991. H. P. Grice, "Logic and conversation", in *Syntax and Semantics 3*, 1975.
