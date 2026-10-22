# 0012: The voice introduction, v2

Status: built behind `VOICE_INTRO_V2` (default false), 2026-10-22. Sources were read on 2026-10-22.

## Context

The owner's verdict on the introduction's video call was that it is a bad experience. The call has these problems:

- **Taking turns.** There is no automatic turn-taking. The browser's speech recognizer runs and a 2.2-second silence timer guesses when an answer ends, or the owner presses "Done answering".
- **The voice.** It is whatever `speechSynthesis` voice the device has, which is often robotic.
- **The questions.** There are four fixed questions with a canned "Got it, thanks." between them. Ava does not react to what was said.
- **Consent.** Every answer is written straight to the notebook.

The owner wants something that feels like a human conversation, as boardy.com does: Ava draws you out, asks follow-ups that build on what you said, and builds a profile. The voice should be formal and natural.

Constraints from earlier ADRs:

- Claude stays the brain, through our provider layer (0007).
- Providers are pluggable through a registry, with a price for every model, so `BudgetGuard` caps spend (0007 §7).
- Keys are per instance, in the vault (0004, 0009). Owners on Superaide AI credits can use platform keys (`brain/platform_ai.py`).
- The browser only talks to the Next.js origin. `/api/*` is proxied, so the session cookie is same-origin (0005).
- Identity, access and what gets saved are decided in code (CLAUDE.md).

## Options (October 2026)

### Turn detection

| Option | Notes |
| --- | --- |
| **Silero VAD in the browser**, [`@ricky0123/vad-web`](https://docs.vad.ricky0123.com/user-guide/browser/) 0.0.31 (ISC) | Runs on the device: free, private, and about 1 ms per 30 ms frame. `onSpeechRealStart` fires once speech has lasted `minSpeechMs`. `onSpeechEnd(Float32Array@16k)` fires after `redemptionMs` of silence. The default model is `legacy`, so pass `model: "v5"`. Defaults to jsDelivr URLs for its `.onnx`, worklet and onnxruntime-web `.wasm/.mjs` files, so set `baseAssetPath`/`onnxWASMBasePath` and serve them yourself. |
| Server VAD (Silero in Python, or the ASR vendor's own endpointing) | Needs a continuous audio stream to the server, which means a WebSocket or WebRTC (see Transport). |
| Semantic end-of-turn models | [Pipecat Smart Turn v3.2](https://github.com/pipecat-ai/smart-turn) (BSD-2): about 8 MB int8 ONNX, about 10–65 ms on CPU, 23 languages. Browser use is undocumented but plausible. [LiveKit's turn detector](https://docs.livekit.io/agents/logic/turns/turn-detector.md): the text model is deprecated, the audio `v1`/`v1-mini` models carry the LiveKit Model License, and `v1` is hosted. [Deepgram Flux](https://deepgram.com/pricing) and [AssemblyAI Universal-Streaming](https://www.assemblyai.com/pricing) have end-of-turn built in, but only on their streaming sockets. |

**Barge-in** practice ([Soniox](https://soniox.com/wiki/turn-taking-barge-in)):

- Echo cancellation comes first.
- Confirm an interruption by its length and confidence, so "mhm" doesn't count.
- On interrupt, stop playback, cancel generation, and cut the assistant's turn back to what was actually heard.

### Speech-to-text

| Option | Price | Notes |
| --- | --- | --- |
| **Deepgram Nova-3** | Pre-recorded $0.0043/min (mono). Streaming $0.0048/min is a "limited-time" promotion; the regular rate is about $0.0077 | REST: `POST /v1/listen?model=nova-3`, `Authorization: Token …`. 40+ languages, plus `multi`. $200 free credit. |
| AssemblyAI Universal-Streaming | $0.15/hour | Billed on socket time, idle included. About 300 ms. End-of-turn built in. |
| OpenAI `gpt-4o-mini-transcribe` / `gpt-4o-transcribe` | $0.003 / $0.006 per minute | OpenAI's realtime transcription is $0.017/min. |
| Browser Web Speech API | free | Chrome and Edge send the audio to the vendor's cloud (in Chrome, Google's). Safari support is partial. Firefox has it off. Brave has no service (always a `network` error). Chrome's on-device `processLocally` exists, but its stable status is unverified. |

### Voices (cost assumes about 900 characters per spoken minute)

| Option | Price | Cost per minute of Ava speaking | Latency (model) |
| --- | --- | --- | --- |
| **ElevenLabs Flash v2.5** | $0.04 / 1K chars | about $0.036 | about 75 ms |
| ElevenLabs v4 Turbo / v3 / Multilingual v2 | v3 and Multilingual v2: $0.08 / 1K; v4 Turbo unverified | about $0.072 | 100–280 ms |
| Cartesia Sonic-3.6 | about $0.037–0.065 / 1K chars | about $0.034–0.045 | under 90 ms claimed, about 166 ms measured by a third party |
| **Deepgram Aura-2** | $0.030 / 1K | about $0.027 | under 200 ms TTFB (unverified). English and Spanish only. |
| OpenAI `gpt-4o-mini-tts`, `tts-1` | n/a | n/a | **Shut down on 2027-01-06**, so don't build on them. |
| Browser `speechSynthesis` | free | 0 | Quality depends on the device. Edge's "Online (Natural)" and Apple's Premium/Enhanced voices are good; the defaults are robotic. |

### All-in-one voice agents

| Option | Price | Claude as the brain? |
| --- | --- | --- |
| OpenAI Realtime (`gpt-realtime-2.1`) | $32 / $64 per 1M audio tokens in/out, roughly $0.02 + $0.08 per minute | No: OpenAI models only. |
| ElevenLabs Agents | $0.08/min plus the LLM | Yes, natively, at the provider's rate. |
| Deepgram Voice Agent | $0.05–0.075/min | Yes, BYO LLM tier. |
| [Pipecat](https://docs.pipecat.ai/learn/llm) (BSD-2), [LiveKit Agents](https://github.com/livekit/agents) (Apache-2.0) | open source orchestration | Yes, with Anthropic plugins. Both need a media server or WebRTC transport and a separate Python worker. |

Every all-in-one product moves the interview logic, the transcript and the spend outside our code and our cap. Running the interview's turns there would also put the owner's answers in a third party's agent platform.

### How Boardy does it

From [TechCrunch, 2024-10-24](https://techcrunch.com/2024/10/24/ai-networking-startup-boardy-raises-3m-pre-seed/), [Creandum](https://creandum.com/stories/backing-boardy-ai) and [boardy.ai](https://boardy.ai):

- **The call.** The original intake was a phone call. Boardy asked about your experience, what you're working on and where you need help, then looked through its network.
- **Introductions.** These are double opt-in: no one hears about a match until both sides say yes.
- **Today.** Intake is mostly text-first (WhatsApp, iMessage, email, Claude and ChatGPT apps), with "Nothing sends until you do."

We take three things from it:

1. A warm, curious interviewer that asks about you, one thing at a time.
2. Follow-ups that build on your answer.
3. Explicit consent before anything leaves the conversation.

No public source describes how Boardy stores its profiles.

## Decision

**Keep Claude as the brain and orchestrate in our own code. Detect turns in the browser. Use plain HTTP for each turn. Make ASR and TTS pluggable, with a free browser fallback.**

```
browser                                         FastAPI (/app/intro/voice/*, owner only)
-------                                         ----------------------------------------
mic --> Silero VAD v5 (on device)
          | speech end: 16 kHz WAV
          | (or text from Web Speech, or typed)
          +-- POST /turn (multipart, cookie + X-Requested-With) -->  ASR (Deepgram | fake)
                                                                     -> Claude turn (streamed)
                                                                     -> sentence splitter
                                                                     -> TTS per sentence
                                                                        (ElevenLabs | Aura | fake)
<-- SSE: transcript, sentence, audio(b64), done ------------------------+
captions, AudioContext playback
talking over her: stop playback, abort, and send `heard` with the next turn
```

1. **Transport is one HTTP request per utterance, streaming the reply back as Server-Sent Events.** Next.js route handlers can't hold a WebSocket (`backend-for-frontend.md`: "WebSockets won't work"), and `rewrites` don't document upgrade proxying. A socket straight to the API port would break the same-origin cookie rule (0005). Because the browser's VAD already cuts the audio into utterances, a POST per turn loses only the overlap of streaming ASR (about 200–300 ms). In return we get the existing session auth, CSRF header and per-instance scope for free, and the proxy already streams bodies (as `/app/chat/stream` does). WebRTC would add a media server for no gain at this scale. If streaming ASR is needed later, the step is a dedicated WebSocket service behind an ingress that proxies upgrades, which checks `Origin` and a short-lived ticket minted by `/app`.

2. **Turn detection runs in the browser with Silero VAD v5.**
   - **Turns.** A turn ends after 0.9 s of silence, and speech under 250 ms is ignored. The model, worklet and onnxruntime files are copied into `/vad/` at build time (`scripts/copy-vad-assets.mjs`), never loaded from a CDN.
   - **While Ava speaks, barge-in is guarded.** The microphone uses `echoCancellation`. The detector needs a probability of 0.8 or more and at least 450 ms of speech. When it fires, playback stops at once, the request is aborted, and the next turn carries `heard`, the part of her reply that was played. The server cuts her last turn down to that and tells Claude "[they interrupted here]".
   - **Semantic end-of-turn models are deferred.** Smart Turn v3 is a candidate once its browser use is confirmed.

3. **Claude conducts the interview, and code holds the plan.**
   - **The plan.** Six topics in `voice/interview.py`: who you are; current work and when you're hard to reach; the people who matter and who always gets through; how to reach you and what's urgent; quiet hours; what Ava may share and what never.
   - **The persona.** Ava is warm, composed and formal. She asks one question at a time, grounded in a specific detail of the last answer, and any topic can be skipped.
   - **Tags, not trust.** Each reply starts with `[follow]`, `[next]` or `[end]`, which code strips before anything is spoken. Code allows at most one follow-up per topic, whatever the model says, and decides when the call ends.
   - **Model and spend.** The turn uses `model_chat` with `max_tokens=300` under the purpose `voice_turn`.
   - **No brain.** Without one (offline, the cap reached, or a provider error), a scripted interviewer asks the same six questions, so the call never dead-ends.

4. **Providers come from a registry, like 0007.**
   - **The protocols.** `voice/providers.py` defines `ASR.transcribe(audio, mime, lang)` and `TTS.synthesize(text, voice, lang)`.
   - **The adapters.** `DeepgramASR` (Nova-3), `ElevenLabsTTS` (Flash v2.5) and `DeepgramTTS` (Aura-2), as thin `httpx` clients with no SDKs, plus fakes.
   - **Choosing.** `choose_asr`/`choose_tts` pick:
     1. the owner's own key (vault, "Connections → Deepgram / ElevenLabs");
     2. else Superaide credits, if the owner opted in and the server has `PLATFORM_DEEPGRAM_API_KEY` / `PLATFORM_ELEVENLABS_API_KEY`. These are never put into an owner's settings, as with `platform_anthropic_api_key`;
     3. else **the browser**: Web Speech recognition where it exists, and the best system voice. That voice is ranked by `scoreVoice`, which prefers neural/Premium voices and formal named voices of the chosen style, and excludes novelty voices.
   - **Fallbacks.** Without any ASR (Brave, Firefox), the call is typed.
   - **Voice settings.** The owner picks a formal female, formal male or neutral voice. The language follows the browser's locale. Aura-2 is skipped for non-English locales.

5. **Spend goes through `BudgetGuard`.**
   - **Purposes.** `voice_asr`, `voice_tts` and `voice_turn` count against a new `voice` daily bucket (US$0.30 by default) as well as the day, month and lifetime caps.
   - **Prices.** Per provider and model in `voice/prices.py`, per audio minute or per 1K characters. A pair with no price is refused.
   - **Reserving.** ASR reserves the longest accepted utterance (`VOICE_MAX_TURN_SECONDS`, 60) and settles at the seconds billed. TTS reserves and settles the exact character cost.
   - **When refused.** Paid speech falls back to the browser's voice for that sentence. Paid ASR returns an error and the client keeps listening. Credit-paid calls carry `key_source="platform"`, so the owner's credit allowance and the platform's daily cap apply.

6. **Consent and privacy.**
   - **Consent.** The call's turns are kept only in the onboarding conversation (events marked `via: call`) and the onboarding row. At the end, `/finish` reads the transcript once with the existing fact extractor (or, offline, keeps each answer in the owner's own words) and proposes the facts. The owner edits, removes or adds lines, and **only `/confirm` saves them** with `remember_fact(source="intro_call")`: owner-only audience, deduplicated. "Don't save anything" saves nothing.
   - **Privacy.** Audio is never logged or stored; it lives in memory for one request. The UI says which service turns speech into text.

## Cost per minute of conversation (speech only)

Assume the owner talks half the time and Ava the other half, at about 900 characters per minute. These figures come from `voice.prices.per_minute`.

| ASR | TTS | Speech cost per call minute |
| --- | --- | --- |
| Deepgram Nova-3 | ElevenLabs Flash v2.5 | **about $0.020** |
| Deepgram Nova-3 | Deepgram Aura-2 (one key) | **about $0.016** |
| Browser | ElevenLabs Flash v2.5 | about $0.018 |
| Browser | Browser | **$0** |

The Claude turns come on top: Haiku 5.5 at $0.10 / $0.50 per million tokens, about 1.5K tokens in and 80 out per turn, which is under $0.001 a turn. A five-minute intro therefore costs about **$0.10** with Deepgram and ElevenLabs.

## Latency

These figures were measured in the E2E suite (Chromium, fake microphone playing real speech, fake ASR/TTS, the scripted interviewer, through the Next.js proxy). From speech end to Ava's first sound, across five runs: **120–222 ms**. That is the pipeline's own overhead. The client records the same number for every real call (`window.__voiceLatency`), and the server reports `asr_ms`, `first_text_ms`, `first_audio_ms` and `total_ms` in each `done` event.

The expected latency with real services, from vendor figures (not yet measured here):

| Stage | Expected |
| --- | --- |
| VAD hangover | 0.9 s, by design. Not counted, as is usual. |
| Upload plus Nova-3 pre-recorded on a 3–5 s clip | 250–400 ms |
| Haiku first sentence | 350–600 ms |
| ElevenLabs Flash for that sentence | 150–300 ms |
| **Total** | **about 0.8–1.3 s**, near the 1.2 s target |

If it runs over, the first things to change are streaming ASR (item 1) and the shorter first sentence the prompt already asks for.

## Consequences

- The old call stays as it was while the flag is off. When the flag is on, `/meet`'s "Start the call" opens the new one.
- The VAD's files (about 13.5 MB: mostly the onnxruntime WASM, plus the 2.3 MB model) load only when a call starts.
- The VAD runs in Chromium, Safari and Firefox (AudioWorklet plus WASM). Speech-to-text without a key works only where Web Speech does, so Brave and Firefox type unless Deepgram is connected.
- With the browser's own voice there is no echo reference, so barge-in relies on the guarded thresholds. Headphones help, and a server voice is better.
- ElevenLabs voice IDs: George (`JBFqnCBsd6RMkjVDRZzb`) is confirmed in ElevenLabs' docs. Alice and River should be checked with `GET /v1/voices` (premade).
- Open: Smart Turn in the browser, streaming ASR through a socket, Cartesia as a third TTS adapter, and an owner-facing voice preview.
