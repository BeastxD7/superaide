# Social-cognition models for cross-channel, multi-party agent memory

Scope note: about 10 searches/fetches, mostly via search snippets. Several primary papers (Wegner 1985/87, Clark & Brennan, Clark & Carlson, FANToM full text, BigToM, OpenToM) were NOT read in full; see Gaps. Anything without a link is in Inferences, not fact.

## 1. Transactive memory systems (who knows what)

### Takeaway
Wegner's TMS is a group-level memory made of each member's memory plus a directory of who knows what and the processes that keep it current. Its processes map directly onto a per-person "knowledge directory" table. Evidence of LLM agents implementing it is thin (one structured-manager paper, one differently-scoped paper).

### Cited Findings
- Wegner proposed TMS in 1985 to explain how groups jointly encode, store and retrieve knowledge. It comprises individual memories, interactions between them, and update processes. Process labels: directory updating (learning who knows what), information allocation (assigning items to members), retrieval coordination. — [Wikipedia: Transactive memory](https://en.wikipedia.org/wiki/Transactive_memory) (secondary; search summary)
- Retrieval in a TMS: when a member lacks the item, they identify an expert via "location information" and ask them. — same search results above ([Wegner, Erber & Raymond 1991 PDF](https://scholar.harvard.edu/sites/scholar.harvard.edu/files/dwegner/files/wegnererberraymond1991.pdf), not read in full)
- "United Minds or Isolated Agents?" emulates a human TMS with a TMS-manager LLM that fills a structured state each round, including an expertise directory with per-agent highlights, and compresses it so agents get summaries rather than full chat history. — [arXiv 2506.06843](https://arxiv.org/pdf/2506.06843) (abstract/snippet level)
- "Multi-Agent Transactive Memory" (2606.19911) uses the term for a shared repository of trajectories (procedural experience), not an expertise directory; reports gains on ALFWorld and WebArena. — [arXiv 2606.19911](https://arxiv.org/pdf/2606.19911)
- LLMA-Mem has a transactive component that models agent capabilities and coordination patterns; mechanism details not verified. — [arXiv 2604.03295](https://arxiv.org/pdf/2604.03295)
- A study of computers as "transactive memory partners" found the extension unsupported in a later study. This is about humans, not agents. — [Academia.edu](https://www.academia.edu/35122371/Agent_Technology_Interactions_Is_the_Computer_a_Transactive_Memory_Partner)

### Inferences
- Table: `knowledge_directory(person_id, topic/entity_id, role ENUM{owner, source, recipient, expert}, confidence, last_confirmed_at, evidence_msg_id)`. Directory updating = a write on every observed "X told me / X said / X owns Y". Allocation = routing a new fact to the person who should hold it ("tell Priya about X"). Retrieval coordination = at answer time, if the asker lacks info, the agent returns "ask Priya" rather than the content.
- For Ava, the directory is mostly of people, not agents: the owner's colleagues' areas. Entries must be sourced and decay unless reconfirmed.

### Gaps
- Wegner 1985/1987 originals and team-performance meta-analyses (e.g., Ren & Argote) not read. No empirical results on agent TMS beyond abstracts.

## 2. Common ground, grounding, participant roles

### Takeaway
Common ground is what participants mutually take as known, built up through grounding acts; listeners are graded (addressee, side participant, overhearer, eavesdropper). That grading is the right schema for channel audiences: DM, private channel, public channel.

### Cited Findings
- Clark & Brennan (1991), "Grounding in communication", in Perspectives on Socially Shared Cognition, pp. 127-149. The cost of grounding varies with the medium. — [Brennan Wikipedia reference list](https://en.wikipedia.org/wiki/Susan_Brennan); [AAAS profile](https://www.aaas.org/finding-common-ground-susan-brennan-and-science-collaborative-communication)
- Clark & Carlson (1982), "Hearers and speech acts", Language 58:332-372, distinguishes participatory roles beyond speaker/hearer. — via [Dynel 2014, Brno Studies in English](https://journals.phil.muni.cz/bse/article/view/21761)
- Common taxonomy: speakers, addressees, side participants; other listeners are overhearers, split into bystanders (known, not taking part) and eavesdroppers (unknown to the speaker). Overhearer vs eavesdropper is by intent/awareness. Terms are not uniform across authors. — [Hokkaido Univ. repository](https://eprints.lib.hokudai.ac.jp/repo/huscap/all/51968/011_OH.pdf); [CNRS glossary](https://icar.cnrs.fr/dicoplantin/wp-json/wp/v2/posts/5407); [Dynel](https://journals.phil.muni.cz/bse/article/view/21761)
- Dynel argues a bystander can be a ratified third party when the speaker intends to communicate with them. — same Dynel link.

### Inferences
- Map: DM = addressee only, common ground {owner, person}. Private channel/group = ratified participants (members at time of message). Public channel = ratified audience plus unratified overhearers (anyone who can later join/search). Rule: "on the record for audience A" = a fact conveyed in a context whose ratified+overhearable set is A.
- Table: `context(id, channel, kind, member_snapshot)`; `disclosure(fact_id, context_id, speaker_id, addressee_ids[], side_participant_ids[], overhearable_set_id, at, grounding_state ENUM{asserted, acknowledged, confirmed, disputed})`. Common ground between two people = facts with a disclosure whose audience includes both and grounding_state >= acknowledged. Store member snapshot at message time, because membership changes.
- Grounding acts become state transitions: an ack ("got it", thumbs-up) moves asserted to acknowledged; a restatement or answer moves to confirmed. Absence of any ack means the agent should not assume the person knows.

### Gaps
- Clark's "Using Language" and Clark & Brennan text not read; "least collective effort" and evidence-of-understanding hierarchy not verified. The search found no source mapping participant roles to Slack explicitly.

## 3. Theory of mind, belief tracking, what LLMs get wrong

### Takeaway
Benchmarks show LLMs are weak at tracking who knows what under information asymmetry, especially in conversation, at higher orders, and after updates. Evidence favors explicit, separately maintained state over prompting alone.

### Cited Findings
- FANToM tests ToM through QA in conversations with information asymmetry; asks multiple question types requiring the same reasoning to expose illusory ToM; SOTA LLMs, including with chain of thought or fine-tuning, perform significantly worse than humans. — [arXiv 2310.15421](https://arxiv.org/abs/2310.15421) (abstract only; no scores retrieved)
- Hi-ToM: questions from 0th to 4th order; performance declines as order increases. — [arXiv 2310.16755](https://arxiv.org/pdf/2310.16755)
- Explicit belief-state representation improved LLM agents' ToM in multi-agent collaboration; GPT-4+Belief answered second-order inferences correctly in nearly 70% of cases. — [arXiv 2310.10701](https://arxiv.org/pdf/2310.10701)
- PDDL-MIND argues failures stem mainly from unreliable implicit state tracking; decouples environment state from belief inference with explicit PDDL states; evaluated on MMToM-QA, MuMa-ToM, FANToM. — [alphaxiv 2604.17819](https://www.alphaxiv.org/abs/2604.17819.md)
- DToM-Track: models infer an agent's current belief but struggle to retrieve prior beliefs after updates (recency/interference), motivating explicit belief-history. — [arXiv 2603.14646](https://arxiv.org/abs/2603.14646)
- OmniToM identifies an actor-specific bottleneck converting narrative facts into each actor's beliefs and shared mental states. — [alphaxiv 2605.26322](https://www.alphaxiv.org/abs/2605.26322)
- Privacy angle: ConfAIde found GPT-4 and ChatGPT leaked private information in contexts where humans would not, at 39% and 57%; persisted with privacy prompts and CoT. — [Mireshghallah et al. 2023 (ar5iv)](https://ar5iv.labs.arxiv.org/html/2310.17884)
- "1-2-3 Check" splits privacy reasoning into Extractor/Checker/Executor; reduced leakage 36% (workshop version, ConfAIde) and 18%/19% (later revision, ConfAIde/PrivacyLens, GPT-4o). Numbers differ across versions; setups not compared. — [arXiv 2508.07667](https://arxiv.org/html/2508.07667v3)

### Inferences
- Dynamic epistemic logic ("A knows B knows p") is only needed to depth 1-2 in practice. Represent as `belief(holder, proposition_id, status ENUM{knows, believes, unaware, believes_false}, source_disclosure_id, believed_version, as_of, order)`; order 1 = person knows p; order 2 = person knows that Z knows p (e.g. a shared thread). Append-only with `valid_from/valid_to` handles the "prior belief after update" failure.
- Algorithm: at reply time, code (not the model) computes the allowed-fact set for the audience from the ledger, and the model only sees that set plus an "unknown to this person" list for conflict checks. Consistent with the repo rule that identity/audience is decided in code.
- Because FANToM-style failures occur even with CoT, a test suite should use deterministic ledger assertions rather than rely on model judgment.

### Gaps
- FANToM numbers, BigToM, OpenToM results not retrieved (PDF fetch unreadable; no BigToM/OpenToM hits). No evidence found on production "ledger" systems beyond the above research prototypes.

## 4. Commitments and prospective memory

### Takeaway
Speech-act theory gives clean types (directive = request, commissive = commitment) for extraction; commitments are harder to label reliably than requests, and extraction works best as suggestions confirmed by a human.

### Cited Findings
- Sentence classification into speech-act types including directive and commissive, via rules plus trained classifier. — [US patent 10706233](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10706233); related [US9170993B2](https://patents.google.com/patent/US9170993)
- Enron email work frames requests and commitments with Speech Act Theory; motivation is managers tracking outstanding promises made, promises received and unmet requests. Annotator agreement: requests kappa 0.78, commitments 0.54 (Lampert, Paris & Dale 2007). — [Macquarie](https://researchers.mq.edu.au/en/publications/can-requests-for-action-and-commitments-to-act-be-reliably-identi/)
- Smart To-Do: classifier returns probability a sentence is a commitment; three annotators, Cohen's kappa 0.694. — [arXiv 2005.06282](https://arxiv.org/pdf/2005.06282)
- Semanta (rule-based email action items) concluded full automation is not feasible; good as reviewable suggestions. — [U08-1009](https://aclweb.org/anthology/U08-1009.pdf)

### Inferences
- Table: `commitment(id, kind ENUM{promise, request, offer}, debtor_id, creditor_id, text, due_at nullable, status ENUM{proposed, open, done, cancelled, overdue}, source_disclosure_id, channel_of_origin, confirmed_by_owner bool, entity_ids[])`. A "proposed" state until confirmed or until classifier confidence passes threshold, matching the suggestion-style finding. Closure detection: link later messages by entity_ids (ticket/PR) and "done/shipped" phrasing. Follow-up scheduler is a code job reading `due_at`, delivering via the channel appropriate to the creditor (respecting audience: do not nag about a private DM commitment in a public channel).
- Prospective memory = the scheduler plus trigger types: time-based and event-based (entity reappears).

### Gaps
- Nothing found specific to Slack commitment extraction, or measured follow-up reliability across channels. LLM-era (2024-26) commitment extraction papers not found in this pass.

## 5. Discourse structure, episodes, consolidation

### Takeaway
Thread disentanglement is solved only moderately; off-the-shelf LLMs are weak zero-shot. Event segmentation by surprise provides a principled way to cut episodes. Entity-keyed linking across channels is not covered by found literature, so design from engineering primitives.

### Cited Findings
- IRC disentanglement corpus: 77,563 Ubuntu IRC messages annotated with reply-to graphs; ACL 2019. — [arXiv 1810.11118](https://arxiv.org/pdf/1810.11118); [TFDS](https://www.tensorflow.org/datasets/catalog/irc_disentanglement)
- Slack threads give only one layer of branching, so most chat remains tangled. — [CASCON 2025](https://conf.researchr.org/details/cascon-2025/technical-papers-track/38/Revisiting-Thread-Disentanglement-in-Technical-Chat-with-Fine-Tuned-LLMs-and-Graph-Ba)
- Fine-tuned LLM predicts reply-to links then graph clustering; 0.90 Shen F-score zero-shot on Discord developer chat. — same CASCON link
- LREC 2026: GPT-4o performs poorly without fine-tuning; fine-tuned GPT-4o mini roughly equals small from-scratch models; F1 74.4% for links, 45.3% for fully matching conversations. — [LREC 2026](https://preview.aclanthology.org/ingest-lrec/2026.lrec-main.229/)
- Bipartite-graph post-processing may beat greedy highest-probability linking. — [arXiv 2112.05346](https://ar5iv.labs.arxiv.org/html/2112.05346)
- Event segmentation: boundaries align with large rises in prediction error; GPT-2 surprise used as a proxy. — [Michelmann, Zacks, Norman, Hasson](https://escholarship.org/content/qt12c888dt/qt12c888dt.pdf)
- EM-LLM segments tokens into events by Bayesian surprise plus graph refinement, retrieves by similarity plus temporal contiguity, outperforms InfLLM on LongBench/inf-Bench, correlates with human boundaries. — [arXiv 2407.09450](https://arxiv.org/html/2407.09450v3); hierarchical follow-up at [ICLR 2025](https://iclr.cc/virtual/2025/33190)

### Inferences
- Table: `topic(id, canonical_key)`, `topic_entity(topic_id, entity_type ENUM{ticket, pr, url, project, person}, value)`, `episode(id, topic_id, start, end, context_ids[], summary)`, `message_link(msg_id, reply_to_id, episode_id, method, score)`. Cross-channel linking: deterministic regex entity extraction (ticket IDs, PR numbers, normalized URLs) merges episodes first; embedding similarity only proposes links needing a threshold. Because plain LLM disentanglement is weak, rely on explicit reply/thread ids, then entity match, then time/participant proximity.
- Linking across contexts must not leak: an episode links to a topic, but each episode keeps its own audience; a summary shown in context B includes only facts with disclosures visible to B's audience.
- Consolidation: episode summaries (audience-scoped) promoted to semantic facts only with provenance; a semantic fact inherits the intersection of audiences of its source episodes unless a disclosure explicitly widens it.

### Gaps
- No literature found on cross-channel/DM linking by entity; no consolidation-of-episodes-to-semantic-facts source found in this pass.

## 6. Social norms as policies and tests

### Takeaway
Contextual integrity is the best-supported formalism: a flow is judged by sender, recipient, information type and transmission principle. The listed human norms translate to rules over the ledger; LLMs alone are unreliable at them.

### Cited Findings
- ConfAIde operationalizes contextual integrity (actor, use, recipient) in tiers up to multi-party secret-sharing, and shows models leak where humans would not. — [ar5iv 2310.17884](https://ar5iv.labs.arxiv.org/html/2310.17884)
- Decomposing privacy reasoning (extract, check, execute) reduces leakage. — [arXiv 2508.07667](https://arxiv.org/html/2508.07667v3)

### Inferences (norm to policy)
- Need-to-know / not repeating a confidence: flow rule `allow(fact, audience)` iff every audience member is in fact.allowed_audience; a fact told in confidence carries `confidence_of = speaker_id` and `transmission_principle = "do not onward-share"`. Test: told X privately, asked by Y, reply must not contain X's fact or confirm it exists.
- Asking before sharing: if the fact is `shareable_with_consent`, outbound path creates a `consent_request` to the speaker and replies with a neutral deferral. Test: reply contains no fact; request row exists.
- Correcting a wrong impression: if `belief(Y, p) = believes_false` and Y is entitled to p, queue a correction in a context whose audience is within p's allowed set; otherwise stay silent.
- Saving face / code-switching: tone is a rendering concern, but fact selection comes from the ledger; test with paired audiences (owner DM vs public channel) asserting different fact sets and identical truthfulness (no false statements to cover gaps).
- Non-confirmation: "no comment"-style refusals must not reveal existence; tests compare replies for secret-present vs secret-absent worlds (should be indistinguishable). Metamorphic test design is my suggestion, not from a source.

### Gaps
- No source found formalizing face-saving or code-switching for agents; treated as design inference only.
