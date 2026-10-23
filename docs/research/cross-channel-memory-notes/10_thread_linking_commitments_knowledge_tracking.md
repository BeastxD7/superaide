# Thread linking, segmentation, commitments and knowledge tracking for Ava (cross-channel multi-party memory)

Reading log (what was actually read in full text vs. only abstract/snippet). Tool note: PDFs were text-extracted locally with pypdf and read by section/table.
- READ (full text, tables checked): FANToM 2310.15421 (all of Sec 3-4, Tables 1-4, 9 not fully dumped); Kummerfeld et al. 2019 ACL P19-1374 (Sec 4-5, Tables 1-5); SeCom (ICLR 2025, arXiv 2502.05589; Tables 1, 4); EM-LLM (ICLR 2025, arXiv 2407.09450; Sec 3, Fig 4, Table 1); CoThinker (arXiv 2506.06843, TMLR 2026; Sec 4.2, 5.6, Table 4); Cohen/Carvalho/Mitchell EMNLP 2004 W04-3240 (Tables 1, 2, 4); Morgan et al. SIGdial 2006 W06-1314 (agreement section, Table 1).
- ABSTRACT/SNIPPET ONLY (numbers below are as reported by abstract or search snippet, NOT verified in full text): CASCON 2025 Aselstyne & El Mezouar; Dialogue Disentanglement in SE (2105.08887); Hi-ToM (2310.16755, no numbers in abstract); Sahar/Hindle/Bezemer JSS 2020 Gitter; DD-GEPA (2606.07894), Takada & Mori 2026, Pal et al. LREC 2026, CODI (Riggio 2023; PDF 403, not read); AutoMin 2025 (2509.13814).
- NOT FOUND/NOT READ: Elsner & Charniak 2008/2010 originals (only via Kummerfeld's comparison), TextTiling original, Enron-specific commitment datasets, any paper on cross-channel (DM + thread + channel) topic linking for an assistant, any LLM-extraction precision/recall on commitments in chat. Stated as gaps below.

---

## 1. Linking the same topic/issue across a DM, a channel thread and a channel

### Takeaway
No primary paper addresses cross-channel (DM to thread to channel) topic linking for an assistant; the closest literature is single-channel disentanglement (reply-to link prediction, F ~0.72 links / ~0.36-0.38 exact-conversation F on Ubuntu IRC) and developer-chat-to-issue references. Best-supported design is a layered linker: deterministic identifiers first (high precision, low recall), then embedding/LLM link proposals that are held as "candidate" until the owner confirms.

### Cited Findings
- Kummerfeld corpus: 77,563 manually annotated IRC messages (74,963 #Ubuntu, 2,600 #Linux), links between message pairs from which conversations are inferred — [ACL P19-1374 Sec 4](https://aclanthology.org/P19-1374.pdf)
- Inter-annotator agreement on link graphs: kappa 0.71 (train subset), 0.72 dev, 0.74 test, Channel Two 0.72; conversation-level 1-1 overlap 83.8 on test, VI 95.0, exact-match F1 49.5 on test — [Table 2, P19-1374](https://aclanthology.org/P19-1374.pdf)
- Link prediction on Ubuntu test: Previous-message baseline F 35.0; Linear 63.5; Feedforward 72.3; x10 vote 73.5 (P 74.9, R 72.2); union R 79.7 — [Table 3](https://aclanthology.org/P19-1374.pdf)
- Conversation-level on Ubuntu test: Feedforward VI 91.3, 1-1 75.6, exact-match P/R/F 34.6/38.0/36.2; x10 vote 91.5/76.0/36.3/39.7/38.0; Lowe 2017 heuristic F 8.9; Elsner & Charniak 2008 F 15.5; intersect P 67.0 but R 21.1 — [Table 4](https://aclanthology.org/P19-1374.pdf)
- Training on 1k random messages (Elsner-sized) drops link F from 72.3 to 63.0 and conversation F from 36.2 to 21.0; std dev rises with fewer, narrower samples (robustness concern) — [Table 5](https://aclanthology.org/P19-1374.pdf)
- Authors note Slack-style threads "have limited depth"/explicit structure, motivating the work for unstructured chat — [P19-1374 Sec 1](https://aclanthology.org/P19-1374.pdf)
- LLM-based, out-of-domain: fine-tuned LLM reply-to link prediction plus graph clustering reaches 0.90 Shen F and 0.87 one-to-one on zero-shot Discord developer chat (abstract only; unverified in full text) — [CASCON 2025](https://conf.researchr.org/details/cascon-2025/technical-papers-track/38/Revisiting-Thread-Disentanglement-in-Technical-Chat-with-Fine-Tuned-LLMs-and-Graph-Ba)
- Existing disentanglement approaches do poorly on technical software chat; four human-judged failure modes: ignoring interaction patterns, contextual information, mixing up topics, ignoring user relationships (abstract only) — [arXiv 2105.08887](https://arxiv.org/abs/2105.08887)
- Search snippets report LLM prompting approaches for disentanglement are substantially inferior to non-LLM methods (Takada & Mori 2026) and that fine-tuned GPT-4o mini underperforms structure-aware models (Pal et al., LREC 2026): unverified, search-snippet level — [DD-GEPA 2606.07894](https://arxiv.org/pdf/2606.07894), [LREC 2026](https://preview.aclanthology.org/ingest-lrec/2026.lrec-main.229/)
- Identifier coverage in practice: 24 Gitter project rooms, 3,133,106 messages, 14,096 issue references (about 0.45% of messages carry an issue reference by my arithmetic); issues discussed in chat take longer to resolve — [Sahar, Hindle, Bezemer JSS 2020](http://asgaard.ece.ualberta.ca/papers/Journal/JSS_2020_Sahar_How_are_issue_reports_discussed_in_Gitter_chat_rooms.pdf) (abstract-level). Gitter natively supports issue/PR linking, so the references are explicit by design; Discord has no native equivalent and needs bots — search snippets, unverified.
- CODI is reported (snippet) as state of the art on IRC, Slack and Discord transcripts — [Riggio et al. 2023](https://www.inf.usi.ch/phd/raglianti/publications/Riggio2023a.pdf); PDF not readable here, numbers not obtained.

### Inferences
Design (best-supported algorithm):
1. Normalise each message to `{channel, thread_ts, author_id, ts, text}`. Within a channel, thread = platform thread id when present (Slack `thread_ts`, Telegram reply chain); otherwise run reply-to link prediction over the last N messages with features time gap, @-mention/addressee, same author, lexical overlap (Kummerfeld features), optionally an LLM scorer. Expect link F ~0.70-0.75 on IRC-like data, exact conversation F only ~0.35-0.40 (Table 3/4), so treat inferred conversation boundaries as soft.
2. Cross-channel layer (no primary evidence; closest-literature design): create a `Topic` node. Tier A (auto-link): shared strong identifiers (ticket id like ACME-142, PR #88, URL, commit SHA, stack-trace/error signature, file path). Tier B (propose): embedding similarity of the message (plus a rolling summary of each open topic) over topics active in last K days, top-1 with margin; proposal stored as `candidate_link(score, evidence)` and shown to owner for confirm/deny. Tier C: no link, stay in own thread.
3. Expected accuracy (my estimates, not measured): Tier A precision >0.95 but covers few messages (Gitter ratio ~0.5% of messages carry an id; coverage of topics is higher, since one id links a whole thread, but unmeasured). Tier B precision unknown; plan on 0.6-0.8 top-1 for same-topic with owner-confirm gate; do not auto-merge.
4. Privacy rule (project-specific): a link never changes audience. Linking client message to an internal topic lets Ava retrieve internal memory, but the audience filter still applies at retrieval; the link itself is only disclosed to the owner.
Failure modes: id collisions across orgs/repos (#88 in two repos), topic drift within a long thread, generic words ("the issue") linking to wrong topic, client paraphrase with no shared tokens, merge of two parallel issues, stale topics. Over-linking is the dangerous direction (leaks via context), so bias to precision.
Log offline: every candidate link with `(msg_id, topic_id, tier, score, margin, evidence ids, channel pair, owner decision, latency to decision)`; owner confirm/deny labels become the evaluation set; also log `would_link_without_confirm` to estimate precision of Tier B at various thresholds; log id-extraction hits/misses per channel.

### Gaps
- No primary paper on DM-thread-channel topic linking for a personal assistant; accuracy for Tier B is an extrapolation.
- Full text of CASCON 2025, CODI, DD-GEPA, Pal et al. not read; no verified Slack/Discord-native numbers.
- Elsner & Charniak original not read; their numbers only via Kummerfeld Table 4/6.
- No measured recall of identifier-based linking in Slack/Discord business chat (Gitter is a developer-centric, link-friendly case).

---

## 2. Segmenting chat into threads and episodes

### Takeaway
Zero-shot LLM segmentation clearly beats classic TextTiling-style methods on dialogue segmentation benchmarks (SeCom Table 4), and segment-level memory units beat turn-level and session-level units on long-conversation QA (SeCom Table 1). Surprise-based boundaries (EM-LLM) were validated on token KV caches and podcast transcripts, not chat, so use them only as a secondary signal.

### Cited Findings
- Dialogue segmentation, unsupervised (Pk/WD lower better; F1 higher better), Table 4: TextTiling Pk 0.470/WD 0.493/F1 0.245 on DialSeg711, 0.441/0.453/0.388 SuperDialSeg, 0.469/0.488/0.204 TIAGE; BayesSeg F1 0.556/0.438/0.366; CSM 0.610/0.381/0.427; SeCom zero-shot LLM (GPT-4 segmenter) Pk 0.093/WD 0.103/F1 0.888 on DialSeg711, 0.277/0.289/0.758 SuperDialSeg, 0.363/0.401/0.596 TIAGE — [SeCom, ICLR 2025, Table 4](https://arxiv.org/pdf/2502.05589)
- Memory-unit granularity (LOCOMO, 4k token budget), GPT4Score: Turn-level BM25 65.58, Session-level BM25 63.16, SeCom (BM25, GPT4-Seg) 71.57; with MPNet: turn 57.99, session 51.18, SeCom 69.33; segmenter ablation MPNet retriever: Mistral-7B-Seg 66.37, RoBERTa-Seg 61.84; Long-MT-Bench+ (1k budget): turn 85.61, session 75.29, SeCom 90.58; full history 54.15 on LOCOMO (worse than retrieval) — [SeCom Table 1](https://arxiv.org/pdf/2502.05589)
- EM-LLM: events = tokens whose surprise (negative log-likelihood) exceeds a threshold, refined by modularity/conductance on attention-key similarity graph, then retrieval = k-NN similarity buffer plus temporally contiguous buffer. Reported to beat NV-Embed-v2 RAG by 30.5% on LongBench and 11.5% on InfiniteBench (Sec 4; claim as authors state) and passkey retrieval to 10M tokens; fixed-length segmentation (InfLLM) is worse than random vs human event reports, surprise-based closer to humans (Fig 4B, Wasserstein distance; human data: audio podcast scripts) — [EM-LLM, ICLR 2025](https://arxiv.org/pdf/2407.09450)
- EM-LLM Table 1 gains over InfLLM are small on some models (e.g., Phi-3.5 LongBench subtotals 31.7 vs 31.8) — [EM-LLM Table 1](https://arxiv.org/pdf/2407.09450)
- Chat thread disentanglement results (Section 1) apply: conversation exact F ~0.38 for best IRC model.

### Inferences
Algorithm: two-level. (a) Thread level: platform thread id where present, else reply-to link model (Section 1). (b) Episode level: within a thread/DM stream, prompt a mid-size LLM with a rubric to emit boundary after turn i (topic shift, new task, long time gap > T hours, new participant set), validate with time gap and participant-change rules; produce segment summaries plus raw turns and index segments (not turns) for retrieval, combining BM25 and embeddings (SeCom shows BM25 > MPNet by up to ~12 GPT4Score points on LOCOMO, so hybrid is cheap insurance). Expected boundary F1 ~0.6-0.9 on cleaner benchmarks (Table 4); chat/Slack boundary F1 should be assumed nearer TIAGE (~0.6) or worse because chats interleave. Do not use token-surprise as primary: no chat evidence.
Failure modes: boundaries at implicit topic returns (A-B-A), very short messages ("ok", "done"), reaction-only replies, bursts of pasted logs, time gaps splitting one task, over-segmentation causing lost context.
Log: predicted boundaries with LLM rationale, time gap, participants; later "retrieval needed neighbour segment" events (signals under-segmentation); owner/auto-corrections; segment length distribution; per-channel Pk/WD on a small hand-labelled sample (~200 turns per channel type).

### Gaps
- No chat-native (Slack) segmentation benchmark with verified numbers; TIAGE/SuperDialSeg are two-person or customer-service dialogue.
- TextTiling original and neural segmenters other than those listed in SeCom Table 4 not read.
- EM-LLM not tested on conversational data; transfer unknown.

---

## 3. Extracting commitments and follow-ups

### Takeaway
Human agreement on commitments is only moderate (kappa 0.72 for Commit vs 0.81 for Request in email; as low as 0.36 for meeting action items), and classic classifiers had weak commitment F1 (0.11-0.47). I found no verified precision/recall for LLM commitment extraction; design for high recall candidate extraction plus owner confirmation, with structured slots (who, to whom, what, due date, source).

### Cited Findings
- Email speech acts (Request, Propose, Amend, Commit, Deliver; Meeting noun): kappa Meeting 0.82, Deliver 0.75, Commit 0.72, Request 0.81, Amend 0.83, Propose 0.72 on doubly-labelled N03F2; single-act messages: Commit kappa 0.78, Request 0.97, Deliver 0.91 — [Cohen, Carvalho, Mitchell EMNLP 2004, Tables 1-2](https://aclanthology.org/W04-3240.pdf)
- Classifier results (5-fold CV, Table 4; error / F1): Commitment (208 pos of 1357) VP 0.15/0.21, AB 0.14/0.44, SVM 0.17/0.47, DT 0.15/0.11; Request F1 0.58-0.69; Delivery 0.76-0.80; Commissive (utterance-level) 0.83-0.85; Directive 0.72-0.78 — [Table 4, W04-3240](https://aclanthology.org/W04-3240.pdf). Authors note commitments average ~1.3 email verbs/message in their corpora and that data is from CSpace/course-project email (not Enron in the portions I read).
- Meeting action items (ICSI): overall kappa 0.364 across 54 meetings, individual meetings from 1.0 to below zero; best maxent F 31.92 using prosodic and fine-grained dialog-act features (Table 1) — [Morgan, Chang, Gupta, Brenier, SIGdial 2006 W06-1314](https://aclanthology.org/W06-1314.pdf). Earlier citation of kappa .36 and .15 for commit acts (Core & Allen 1997) via search snippet: [Purver et al. 2006](https://preview.aclanthology.org/fix_video/W06-1314.pdf) (snippet, unverified).
- AMI action-item annotation: a slide reports 101 AMI meetings with 381 action items (snippet, unverified); no agreement found.
- Enron: a snippet attributes request/commitment definitions drawn from speech-act theory with two manual annotation experiments on Enron to an AAAI paper — [aaai.org/?p=83569](https://aaai.org/?p=83569) (snippet, not read).
- AutoMin 2025 evaluates LLMs on structured meeting minutes, no action-item F1 obtained — [arXiv 2509.13814](https://arxiv.org/abs/2509.13814).

### Inferences
Algorithm: (1) Cheap trigger filter on first-person future/modal patterns, "can you", "by Friday", "I'll", "we will", plus any message in a thread where the owner is addressed with a request. (2) LLM structured extraction with slots `{kind: commit|request|deliver|amend, committer_id, beneficiary_id, action, due (absolute, resolved against message ts and timezone), conditions, source_msg_id, confidence}`; committer and beneficiary come from platform ids, never from text (project rule). (3) Lifecycle: `proposed -> confirmed -> done|cancelled|overdue`; completion detected by Deliver acts or owner marking; follow-up scheduled at due minus lead time and at due plus grace; stale after N days with owner nudge. (4) Commitments made by the owner to an external party (client) are higher risk: require owner approval before Ava states or promises anything.
Expected accuracy: with kappa ~0.7-0.8 between humans, ceiling on label agreement is roughly that; I expect LLM F1 around 0.6-0.8 for explicit commitments, lower for implicit ones. This is an estimate, not a verified result. Rule: auto-create only as "proposed".
Failure modes: hedges ("I'll try"), conditional promises, quoted/hypothetical commitments, commitments by groups ("we"), relative dates ("next Friday"), cancellation, commitments split across messages, wrong beneficiary in group channels.
Log: every extracted candidate with confidence, extraction prompt/version, owner action (confirm, edit due, dismiss), later completion evidence, time-to-confirm; false-negative probes: owner manually adds a commitment Ava missed; weekly sample of 50 messages double-labelled.

### Gaps
- No verified precision/recall for modern LLM commitment extraction in chat or email found; no Enron-specific numbers read; AMI action-item agreement not found.
- Full text of Enron commitment work and of any 2024-2026 action-item benchmark not read.

---

## 4. Tracking who knows what

### Takeaway
FANToM shows LLMs fail at information-asymmetry questions in multi-party conversation (best GPT-4 variant 26.6 "All Question Types" with CoT vs human 87.5), mainly by including characters who were absent; so who-knows-what must be an explicit ledger computed in code from channel membership and message delivery, not inferred by the model. The multi-agent "transactive memory" paper (2506.06843) is about summary compression, not belief tracking.

### Cited Findings
- FANToM: 256 conversations, 1,415 BeliefQ[Dist./Choice], 703 each FactQ/AnswerabilityQ[List]/InfoAccessQ[List], 2,689 Y/N questions; mean 13.8 turns (short), 21.9 words per turn; information asymmetry created by characters leaving and rejoining — [FANToM 2310.15421 Sec 3.5](https://arxiv.org/pdf/2310.15421)
- Table 1 (short context, All question types / All Answerability / All InfoAccess): Human 87.5 / 90.6 / 90.6; GPT-4 0613 + CoT (June) 26.6 / 40.2 / 57.7; GPT-4 (Oct) + CoT 14.8 / 31.4 / 41.1; ChatGPT + CoT 3.7 / 20.7 / 17.1; Llama-2 Chat + CoT 0.4 / 6.0 / 7.8; fine-tuned Flan-T5 XL 53.7 / 55.9 / 54.4 — [Table 1](https://arxiv.org/pdf/2310.15421)
- Y/N questions (F1), Table 2: GPT-4 (June) Answerability 85.9, InfoAccess 90.3; ChatGPT 64.2/73.2; Mistral 7B 61.5/70.4 — models do better on binary than on list questions, where all-or-nothing scoring applies — [Table 2, Fig 2](https://arxiv.org/pdf/2310.15421)
- Illusory ToM: models do well on BeliefQ[Choice] but much worse on the prerequisite list questions (who has access); free-response scores below or near 50 random baseline on binary choice; with full (long) conversation context performance drops further (search plus ToM combined) — [FANToM Sec 4.1](https://arxiv.org/pdf/2310.15421)
- Errors: for list questions models more often include unaware characters than exclude aware ones; CoT reduces false positives of that kind but increases exclusion of aware characters — [Fig 4-5, Sec 4.2](https://arxiv.org/pdf/2310.15421)
- Per-character consistency (Table 3): ALL FOR EACH CHARACTER GPT-4 (June) 53.2, with CoT 59.2; answer consistency 66.8 / 73.4; ChatGPT 35.0; Llama-2 27.1 — [Table 3](https://arxiv.org/pdf/2310.15421)
- BeliefQ first vs second order (Table 4): GPT-4 (June) 63.8 first, 66.7 second; Falcon + CoT 67.9 / 76.3; second-order cyclic easier than acyclic — [Table 4](https://arxiv.org/pdf/2310.15421)
- Hi-ToM: performance declines on higher-order ToM; no numbers in abstract — [arXiv 2310.16755](https://arxiv.org/abs/2310.16755)
- 2506.06843 "United Minds or Isolated Agents" (CoThinker, TMLR 2026): its Transactive Memory System is an evolving structured summary mu(t) of consensus, expertise cues and open issues shared among agents, to reduce context load; ablation shows TMS less effective for newer GPT models that refuse to expose intermediate reasoning (Table 4: Qwen3-30B-A3B 81.87, GPT5-Nano 55.97, GPT-OSS-20B 57.15 with TMS+Style on). It does not measure who-knows-what accuracy — [arXiv 2506.06843 Sec 4.2, 5.6](https://arxiv.org/pdf/2506.06843)

### Inferences
Algorithm (best supported): a deterministic knowledge ledger. Row = `(fact_id, person_id, channel_id, msg_id, access: witnessed|told|inferred|unknown, ts, retracted)`. Access is derived from delivery facts in code: who was a member of the channel/DM at message time (join/leave events), who was @-mentioned, who read (if platform exposes read). `knows(person, fact)` is true only on witnessed/told rows; never from model inference. Answer questions like "does Carol know the fix date?" via ledger lookup, then let the model phrase. Cross-channel: knowledge of a person is the union over channels they were in, but audience of a reply is the intersection with the current channel members (a #client-y reply must be fit for Carol and any other member). Absent-then-rejoined users are modelled by timestamped membership, mirroring FANToM's setup. Expected accuracy: near 100% on membership-derived access (it is a database query); residual error sits in fact identification (does the message contain fact F?), where LLM extraction accuracy should be assumed ~0.8-0.9 (estimate). Compared with FANToM, avoiding model-inferred access sidesteps the 26.6 vs 87.5 gap, but forwarded/screenshot/verbal knowledge outside the platform is invisible.
Failure modes: knowledge outside the system (phone call, meeting), forwarded messages, edited/deleted messages, bots or integrations posting into channels, channel membership drift, people who read but have notifications muted (membership is not awareness), fact paraphrase mismatch, second-order beliefs (what Bob thinks Carol knows) — keep to first order only.
Log: each disclosure decision `(requester_id, channel, fact_ids considered, ledger verdict, model reply contains fact?, post-hoc leak check result)`; a canary check that diffs reply text for facts whose ledger access for the audience is false; owner corrections "she already knew".

### Gaps
- FANToM Table 9 (full per-model, per-metric, full-context numbers) was not extracted in full here.
- Hi-ToM numbers not read; no read of explicit-belief-state methods papers (e.g., SymbolicToM, belief-state tracking approaches): not covered, no claim made.
- No paper measured ledger-based "who knows what" in a real assistant; transactive-memory LLM paper found does not do this.

---

## Combined minimal tests (scripted multi-party scenario)

Scenario: owner Alice (developer, org X) DMs colleague Bob about ACME-142 (payment webhook timeout), posts a no-id update in #internal, and Carol (org Y) asks in #client-y without an id. These are reference-implementation sketches for the algorithms above (stand-in lexical "embedding"), runnable with plain pytest; replace stand-ins with real components.

```python
import re
from dataclasses import dataclass, field

@dataclass
class Msg:
    id: int; ts: int; channel: str; author: str; text: str
    members: frozenset  # channel membership at send time

ALICE, BOB, CAROL = "u_alice", "u_bob", "u_carol"
DM, INTERNAL, CLIENT = "dm:alice-bob", "#internal", "#client-y"
M = lambda *u: frozenset(u)
CONVO = [
    Msg(1, 100, DM, ALICE, "ACME-142 webhook timeout: root cause is our retry storm. I'll ship the fix by Friday.", M(ALICE, BOB)),
    Msg(2, 110, DM, BOB, "ok, PR #88 is open for it", M(ALICE, BOB)),
    Msg(3, 200, INTERNAL, ALICE, "Update: the webhook timeout fix is in PR #88, deploying Friday.", M(ALICE, BOB)),
    Msg(4, 300, CLIENT, CAROL, "Any news on the payments webhook timeout we reported?", M(ALICE, CAROL)),
]
IDS = re.compile(r"\b(?:[A-Z]{2,}-\d+|PR #\d+)\b")
ID_TO_TOPIC = {"ACME-142": "T1", "PR #88": "T1"}   # PR body references the issue (from VCS)

def extract_ids(t): return IDS.findall(t)

def tier_a_topic(m):
    topics = {ID_TO_TOPIC[i] for i in extract_ids(m.text) if i in ID_TO_TOPIC}
    return topics.pop() if len(topics) == 1 else None

def sim(a, b):  # stand-in for embedding cosine
    tok = lambda s: set(re.findall(r"[a-z]+", s.lower())) - {"the", "is", "in", "on", "we", "any", "of"}
    A, B = tok(a), tok(b); return len(A & B) / max(1, len(A | B))

def link_candidates(msgs, topic_summaries, thr=0.2):
    out = []
    for m in msgs:
        t = tier_a_topic(m)
        if t: out.append((m.id, t, "A", 1.0)); continue
        best = max(topic_summaries.items(), key=lambda kv: sim(m.text, kv[1]))
        s = sim(m.text, best[1])
        if s >= thr: out.append((m.id, best[0], "B", s))   # needs owner confirm
    return out

COMMIT = re.compile(r"\bI'll\b(?P<what>.*?)\bby (?P<due>\w+)", re.I)
def extract_commitments(m):
    r = COMMIT.search(m.text)
    return [dict(committer=m.author, due=r["due"], src=m.id, status="proposed")] if r else []

def ledger(msgs):  # who has access to which message, derived from membership only
    return {m.id: set(m.members) for m in msgs}
def knows(led, person, msg_id): return person in led[msg_id]

# ---- tests
def test_tier_a_links_dm_and_internal_by_identifier():
    links = {i: (t, tier) for i, t, tier, _ in link_candidates(CONVO, {"T1": ""})}
    assert links[1] == ("T1", "A") and links[2] == ("T1", "A") and links[3] == ("T1", "A")

def test_client_message_gets_tier_b_proposal_not_auto_link():
    summ = {"T1": "payments webhook timeout retry storm fix PR deploy"}
    c = [x for x in link_candidates(CONVO, summ) if x[0] == 4]
    assert c and c[0][1] == "T1" and c[0][2] == "B"       # proposal only; owner must confirm

def test_commitment_extracted_as_proposed_with_platform_ids():
    cs = extract_commitments(CONVO[0])
    assert cs == [dict(committer=ALICE, due="Friday", src=1, status="proposed")]
    assert not extract_commitments(CONVO[3])               # client question is not a commitment

def test_carol_does_not_know_dm_or_internal_content():
    led = ledger(CONVO)
    assert not knows(led, CAROL, 1) and not knows(led, CAROL, 3)
    assert knows(led, CAROL, 4)

def test_reply_in_client_channel_must_not_include_facts_only_in_dm():
    led = ledger(CONVO)
    reply_sources = [3]                                    # facts the draft reply cites
    assert any(not knows(led, CAROL, s) for s in reply_sources)   # leak detector fires -> block/ask owner
```
Expected: all five pass; the last asserts the leak detector flags a draft citing the #internal message (id 3) for a #client-y audience, so Ava must ask Alice before disclosing the fix date.

Evaluation harness suggestion: replay real exports with owner labels for (a) link confirm/deny, (b) commitment confirm/dismiss, (c) leak canaries; report precision at fixed recall for Tier B, per-act commitment F1, and zero-tolerance leak count.
