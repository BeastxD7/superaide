# Contextual integrity and privacy-aware memory for a one-owner, many-correspondent assistant

Scope note: ~16 tool calls. Most search results were summaries; numbers below are only those that appeared in retrieved text. Anything not seen in a primary source is in Gaps. Dates: today is 2026-10-23.

## 1. Contextual integrity (CI) and how it has been operationalised for LLM agents; leakage rates

### Takeaway
CI frames a disclosure as an information flow with five parameters (sender, recipient, subject, information type, transmission principle). LLM benchmarks (ConfAIde, CIMemories, PiSAs) show frontier models fail CI judgments at substantial rates even when prompted for privacy, so the decision belongs in code and the model should only act on pre-filtered context.

### Cited Findings
- CI's five parameters: sender, recipient, data subject, information type (attribute), transmission principle (constraint such as confidentiality, consent, notice, legal mandate, subject's stated preference). Actor and attribute values come from context-specific vocabularies. A flow matching the context's informational norms preserves integrity; a departure calls for further evaluation. — [Privacy in Context glossary](https://privaci.info/glossary); [Nissenbaum, Contextual Integrity Up and Down the Data Food Chain (2019)](https://www.degruyterbrill.com/document/doi/10.1515/til-2019-0008/pdf); [Operationalizing CI](https://sos-vo.org/node/48159). (Secondary summaries; original "Privacy in Context" text was not retrieved.)
- Nissenbaum stresses all five parameters matter together; CI is not reducible to subject control or a public/private label. — [Nissenbaum 2016 slides](https://isp.cs.ru.nl/2018/2016/nissenbaum.pdf)
- ConfAIde (Mireshghallah et al., "Can LLMs Keep a Secret?") has four tiers; tiers 1-2 draw on Martin and Nissenbaum's work on privacy expectations, later tiers add theory-of-mind style reasoning. GPT-4 and ChatGPT disclosed private information in contexts where humans would not at 39% and 57% respectively, and leakage persisted with privacy-focused prompts and chain-of-thought. — [arXiv 2310.17884](https://arxiv.org/pdf/2310.17884) (headline figures via search summary; per-model tables not checked)
- "1-2-3 Check" (arXiv 2508.07667): multi-agent reasoning pipeline evaluated on ConfAIde and PrivacyLens; with GPT-4o best configuration cut private-info leakage by 18% (ConfAIde) and 19% (PrivacyLens) versus baselines. A prompting/model-side mitigation only; residual leakage remains. — [arXiv 2508.07667](https://arxiv.org/html/2508.07667v3)
- CIMemories (Meta FAIR, arXiv 2511.14937, ICLR 2026): synthetic profiles averaging 147 attributes over nine domains, 49 tasks each with a purpose and recipient; binary CI labels per attribute-task pair. Frontier models show up to 69% attribute-level violations; lower violation rates often come at the cost of task utility; violations are measured as they accumulate over repeated tasks and memory composition. — [arXiv abs](https://arxiv.org/abs/2511.14937v1); [ICLR poster](https://iclr.cc/virtual/2026/poster/10008868)
- PiSAs (arXiv 2607.05318, July 2026): benchmark of cross-user "data spillage" in shared agent systems (outputs, inter-agent messages, shared memory, user-agent interaction). Each item has two CI annotations: does the info fit the task, and which users may legitimately access it. Finding: system design improves CI compliance but state-of-the-art models still do not reliably filter inappropriate content or restrict transmission to authorised users. Abstract gives no numbers. — [arXiv](https://arxiv.org/abs/2607.05318)
- MEXTRA (arXiv 2502.13172): black-box memory-extraction attack that pulls private interaction records from agent memory; abstract reports effectiveness on two agents but no figures retrieved. — [arXiv](https://arxiv.org/abs/2502.13172)
- MemoryGraft (arXiv 2512.16962, Dec 2025): implants fake "successful experiences" in long-term memory; retrieval (lexical + embedding) then makes the agent adopt them, persisting across sessions; a few poisoned records can be a large share of retrieved experiences on benign workloads (tested on MetaGPT DataInterpreter with GPT-4o; not independently verified). — [arXiv](https://arxiv.org/pdf/2512.16962); [Promptfoo entry](https://promptfoo.dev/lm-security-db/vuln/grafted-experience-drift-bf72230e/)
- Follow-ups exist on memory enforcement: MemLineage (lineage-guided enforcement for agent memory) [arXiv 2605.14421](https://arxiv.org/pdf/2605.14421); forged-reasoning attacks on memory [arXiv 2607.05029](https://arxiv.org/pdf/2607.05029). Titles only; contents not read.
- Information-theoretic view: individually safe agent outputs can jointly reveal sensitive attributes (composition/aggregation leakage). — [arXiv 2603.05520](https://arxiv.org/pdf/2603.05520) (via search summary)
- Simon Willison's "lethal trifecta" (16 Jun 2025): private data + untrusted content + external communication in one agent makes exfiltration via prompt injection possible; he is sceptical of detection-based guardrails. — secondary coverage: [HiddenLayer](https://www.hiddenlayer.com/research/the-lethal-trifecta-and-how-to-defend-against-it), [AI Weekly](https://aiweekly.co/alerts/willison-names-lethal-trifecta-behind-ai-agent-data-leaks) (original post not fetched)
- OWASP LLM02:2025 Sensitive Information Disclosure: system-prompt restrictions on what the model may return "may be ignored or bypassed through prompt injection"; recommended controls are sanitisation, access controls, redaction/pattern detection. Secondary sources flag RAG without per-user authorisation checks and cross-user bleed in multi-tenant apps. — [OWASP](https://genai.owasp.org/llmrisk/llm022025); [ByteHide](https://docs.bytehide.com/products/radar/sast/owasp/llm02-2025)

### Inferences
- Mapping to Ava: sender = the person whose fact was learned (or the channel it arrived through), subject = whom the fact is about, recipient = current correspondent, information type = a typed attribute (calendar, health, finance, contact, work), transmission principle = the audience/consent label attached when learned. Four of five can be stored as columns; only "information type" classification may need a model, and it should happen at write time, not at answer time.
- Because benchmarks show 39-69% inappropriate disclosure when the model decides, a model-in-the-loop CI judgement is not an acceptable enforcement point. The repo's existing rule (filter in SQL before the prompt) matches the pattern all enterprise vendors use. The "never share" free-text list pasted into non-owner prompts (known top-priority leak) contradicts this: it both hands the secret to the model and relies on the model to withhold it.
- Cumulative/compositional leakage (CIMemories, info-theoretic paper) means per-memory filtering is necessary but not sufficient; derived summaries need their own labels (see section 4).

### Gaps
- Per-model numbers for ConfAIde tiers and CIMemories, and MEXTRA/PiSAs numeric results, not retrieved (PDF was compressed, abstracts lack figures).
- No peer-reviewed benchmark found specifically for a one-owner assistant talking to many third parties across channels; PiSAs (shared org agent) is the nearest.
- Original "Privacy in Context" text and Willison's original post not read directly.

## 2. Permission-aware retrieval in enterprise assistants and where it failed

### Takeaway
Every major vendor enforces the requesting user's ACLs at retrieval time and says the model only sees what that user can already open. Failures came not from ACL bugs but from (a) untrusted content steering the model while it held the victim's permissions (Slack AI, EchoLeak) and (b) oversharing, where existing permissions were broader than intended.

### Cited Findings
- Microsoft Copilot: surfaces only organisational data the user has at least view permission to; Semantic Index "honors the user identity-based access boundary so that the grounding process only accesses content that the current user is authorized to access"; honours Purview sensitivity-label/IRM usage rights; permissions extended to external users via Teams shared channels are included; interaction history is stored and deletable; admins retain via Purview. Also uses XPIA/jailbreak classifiers, which "may not be available in all scenarios." Docs also say it is on the user to get SharePoint permissions right. — [Microsoft Learn](https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-privacy) (page updated 2026-10-08)
- EchoLeak (CVE-2025-32711): zero-click data exfiltration from M365 Copilot context, CVSS 9.3, found by Aim Security, who call it an "LLM Scope Violation" enabling indirect prompt injection; Microsoft fixed server-side; no evidence of in-the-wild exploitation. "First zero-click AI exploit" claim not independently verified. — [The Hacker News](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html); [SOC Prime](https://socprime.com/blog/cve-2025-32711-zero-click-ai-vulnerability/)
- Slack AI: docs state features use only data the member can access at request time, show nothing from private channels/DMs the user is not in, and AI search returns nothing normal search would not; uses RAG so only needed data goes to the LLM. — [Slack Help](https://slack.com/help/articles/28310650165907)
- Slack AI failure (PromptArmor, Aug 2024): attacker posts an injection in a public channel; when a victim later queries, the model, running with the victim's access to a private channel, was steered to emit a link carrying the secret (e.g. an API key) to the attacker. Slack initially judged it intended behaviour because public-channel messages are searchable workspace-wide; reported patched afterwards (patch details not confirmed). — [PromptArmor](https://promptarmor.com/resources/data-exfiltration-from-slack-ai-via-indirect-prompt-injection); [The Register](https://www.theregister.com/2024/08/21/slack_ai_prompt_injection/); [Simon Willison](https://simonwillison.net/2024/Aug/20/data-exfiltration-from-slack-ai/)
- Glean and generic pattern: no Glean primary doc retrieved. Third-party descriptions: ACLs recorded at indexing, checked again at query time, with stale-permission windows minimised by frequent refresh, event-driven updates and revalidation before answer generation. — [Texta](https://www.texta.ai/blog/ai-enterprise-search-permissions); [Knostic on Glean](https://www.knostic.ai/blog/glean-data-security) (vendor/secondary)
- Knostic: LLMs can combine individually permitted content into contextually inappropriate output, a gap traditional access control does not cover. — [Knostic](https://www.knostic.ai/blog/glean-data-security) (secondary)

### Inferences
- Shared architecture: identity -> ACL filter inside the retrieval query -> only then prompt. The filter is on the viewer, not the data origin. For Ava the "viewer" is each correspondent, not the owner, which is stricter than M365 Copilot (where viewer = the person whose data it is).
- The Slack/EchoLeak lesson maps directly: any inbound message from an untrusted sender is injection surface; Ava answers strangers while holding owner-level context. Mitigations are architectural: per-turn context is built from the correspondent's audience, not the owner's; no outbound links/tools reachable from low-trust turns; side-effecting actions gated in code (already in `policy/actions`). That removes legs of the lethal trifecta per turn.
- Stale-permission risk analogue: when a person's trust level or a fact's audience changes, derived/cached artefacts (summaries, embeddings) must be invalidated or re-labelled.

### Gaps
- Google Gemini for Workspace and Atlassian Rovo documentation not retrieved (budget); Glean primary docs not retrieved; Slack Connect/external-org behaviour of Slack AI not confirmed; Slack's current status of the 2024 issue not verified.
- Microsoft's own EchoLeak advisory not fetched.

## 3. Audience-at-time-of-learning vs audience-at-time-of-asking; information barriers

### Takeaway
Law and finance solve this with matter-scoped barriers inherited from a system of record; AI vendors translate that into enforcement at retrieval, context and output layers, fail-closed, with audit logs. Mapping: tag each memory with the audience (who was in the room) at write time, and gate on the asker's membership at read time.

### Cited Findings
- Harvey: enforce walls "at the retrieval layer, the context layer, and the output layer"; treat the matter as "a hard security boundary," not a metadata tag; inherit walls from the conflicts/DMS systems; fail closed (skip a document whose membership cannot be confirmed); prior context from one matter can contaminate another, and agents lack human self-recusal; log every context window and make outputs traceable to a matter. Vendor source promoting its own product. — [Harvey](https://www.harvey.ai/blog/agents-and-ethical-walls)
- Intapp/Harvey integration: walls often fragmented across file, email, matter search; an AI shared workspace can bypass them. Practitioner guide recommends screens apply to retrieval, memory, caches, embeddings, logs, evaluations and generated work product, with deny tests and recertification. Failures link to ABA Model Rule 1.10 disqualification risk. — [Intapp](https://www.intapp.com/blog/intapp-walls-harvey-integration/); [ITECS](https://itecs.ai/insights/legal-ai-agents-protect-privilege-ethical-walls)
- Microsoft's model grants by current permission at ask time (interaction records are stored with citations to grounding sources). — [Microsoft Learn](https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-privacy)

### Inferences
- No source found that formalises "audience at time of learning" for agents; this is an inference from CI (transmission principle is set by the original context) plus ethical-wall practice. Suggested model: each memory row stores `audience_at_learning` (set of person IDs / channel + membership snapshot, or a coarse tier: owner-only, named-people, team, public), `subject_id`, `source_channel`, `info_type`. Read rule: allowed iff asker in audience set, or asker is the subject, or owner has explicitly widened it. Channel membership should be snapshotted (a Slack channel's roster changes) and retention of the snapshot is itself personal data.
- "Need to know" = a second, task-purpose condition (CIMemories: an attribute right for one task is wrong for another); implement as an allow-list of info types per purpose/recipient role (client-facing vs internal), not model judgement.
- Client/internal separation maps to a matter/"workspace" tag; memory learned in one matter must not be retrievable in another without explicit owner release, as Harvey's contamination point implies.

### Gaps
- No peer-reviewed guidance found on snapshotting audience at learning time; no regulator rule on AI retrieval behind ethical walls (noted by search). Finance (Chinese walls/MNPI) sources not retrieved.

## 4. Disclosure decision patterns (allow / ask owner / refuse / paraphrase), never-share lists, derived data

### Takeaway
Evidence supports deterministic gating before the model (OWASP says prompt-level restrictions can be bypassed) with outcomes allow / ask owner / refuse / reduce-to-public-level. Specific research on summary-label inheritance is thin; I rely on ethical-wall and lineage work and mark the rest as design inference.

### Cited Findings
- OWASP: prompt restrictions on returnable data "may be ignored or bypassed"; use redaction/pattern matching and access control instead. — [OWASP LLM02](https://genai.owasp.org/llmrisk/llm022025)
- CIMemories: lowering violations often costs task utility, i.e. the allow/refuse trade-off is real and measurable. — [arXiv 2511.14937](https://arxiv.org/abs/2511.14937v1)
- Harvey: output-layer enforcement to keep walled information out of derived work product; every output traceable to a matter. — [Harvey](https://www.harvey.ai/blog/agents-and-ethical-walls)
- MemLineage proposes lineage-guided enforcement for agent memory (provenance of memory entries used for enforcement); content not read. — [arXiv 2605.14421](https://arxiv.org/pdf/2605.14421)

### Inferences (design, not cited)
- Decision function `decide(asker, memory)` returns ALLOW, ASK_OWNER (owner approval via a trusted channel, one-time, logged), REFUSE (fixed neutral text; do not confirm existence of the fact, to avoid a presence oracle), or DOWNGRADE (serve a pre-computed public-level version stored alongside the full fact, written at learning time or by the owner; never "paraphrase on the fly" from the secret, as a model summarising a secret can leak it).
- Never-share list: store as typed rules (subject, info type, or entity IDs) applied as a SQL predicate, never injected as prose into non-owner prompts. Because naming a topic in a prompt discloses it, the list should not enter any non-owner prompt at all. Residual risk: the model may volunteer facts from the user's own message; add an output scan (exact/entity match against deny-list) as defence in depth, acknowledging it is best-effort.
- Summaries/derived facts: audience(derived) = intersection of source audiences (i.e. the strictest), with `derived_from` lineage; if any source is deleted, re-label or delete the derived item; wide-audience summaries need an explicit declassification step by the owner. Embeddings and caches inherit labels too (ITECS/Intapp list).
- Differential audiences for one derived fact: keep separate rows per audience tier (full, redacted) rather than one row filtered at output.
- Memory writes from untrusted senders: label provenance and trust level; do not let unverified content write into the owner-wide tier (MemoryGraft/poisoning).

### Gaps
- No empirical study found testing the "summary inherits strictest audience" rule or paraphrase-to-public-level mechanisms in agents.

## 5. Third-party personal data in a personal assistant (GDPR/CCPA); Slack Connect and external users

### Takeaway
People who message the assistant are data subjects who never signed up; regulatory coverage I could verify is thin. Practitioner sources say Art. 14 notice and erasure reach agent memory, but whether the household exemption applies to an assistant serving a private owner was not resolved.

### Cited Findings
- GDPR Art. 14 applies to personal data not obtained from the data subject; notice generally within a reasonable period and at latest one month, or at first communication with the subject if earlier; narrow exemptions exist. Practitioner source says it applies equally when the processor is an AI agent. — [Legiscope](https://mta-sts.legiscope.com/blog/gdpr-article-14-third-party.html); [Waxell](https://www.waxell.ai/blog/gdpr-ai-agents-transparency-compliance) (secondary)
- Erasure must reach agent memory, vector stores and logs, not only the source row. — [Atlan](https://atlan.com/know/ai-agent/gdpr-compliance-for-ai-agents/) (vendor guidance, not regulator text)
- Waxell reports an EDPB coordinated enforcement on Arts. 12-14 launched 19 Mar 2026 with 25 DPAs; unconfirmed against an EDPB page. — [Waxell](https://www.waxell.ai/blog/gdpr-ai-agents-transparency-compliance)
- Microsoft: interaction history deletable by the user and retention controllable by admins via Purview; shared-channel external users' access is governed by the same permission model. — [Microsoft Learn](https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-privacy)

### Inferences
- Design implications: a per-person record with `subject_id`, purpose, retention and source; a subject-access and erase path that cascades to memories, derived summaries, embeddings and logs (this is where `derived_from` lineage pays off); first-contact notice in the reply flow (one fixed sentence about what Ava stores and how to delete); purpose limitation = use facts learned from a colleague only for tasks serving the owner's stated purpose; short default retention for strangers (the current stranger-mode of "no memory" is the strongest posture). If the owner is a business rather than a private person, the household exemption likely does not apply (my inference; not verified).
- Slack Connect/external users: assume Slack's permission model treats them as separate-org members with channel-scoped access; Ava should treat a Slack Connect user as an external audience tier keyed by the immutable user ID plus team ID, never by display name, and never inherit internal audience.

### Gaps
- Did not retrieve GDPR text (Arts. 2(2)(c), 6, 14, 17), EDPB opinions (e.g. on AI models), CCPA/CPRA provisions, or CJEU household-exemption case law (Lindqvist, Ryneš). Treat all legal points as unverified secondary summaries; legal review needed.
- No source found on Slack Connect behaviour with AI features or on retention norms for third-party conversation data.
