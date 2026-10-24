# Two-channel voice (#125)

Live voice is a thin shell. It turns speech into text, hands the text to the usual agent, and speaks back what it is given. It holds no tools, no memory and no policy. Anything the agent proposes from a call becomes an ordinary approval marked `origin: voice`, and **only a tap inside the Superaide app confirms it**. A spoken yes, a Telegram tap and a Slack tap are all refused (fail closed, tested in `backend/tests/voice/test_voice_provider.py`). A "no" is accepted from anywhere. The call never says "done" about something that is still waiting: such a reply is replaced by the read-back, which says nothing has happened.

There is no on/off flag (#448). Voice turns on when a provider key is present (`VOICE_PROVIDER_API_KEY`, `DEEPGRAM_API_KEY` or the Deepgram key saved under Connections) and is off, with a hint saying what to add, when there is none. Without a key nothing is sent to any vendor and nothing is bought. The cap and the app-only confirmation rule are unchanged.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `VOICE_PROVIDER` | `auto` | `auto` (deepgram when a key is present, else off), `none` (force off), `fake` (scripted, offline), `deepgram` (the hosted adapter below) or `realtime` (adapter stub, no vendor wired) |
| `VOICE_PROVIDER_API_KEY` | empty | The vendor's key (falls back to `DEEPGRAM_API_KEY`). Normally saved in the vault under the `voice_provider` connection |
| `BUDGET_MONTHLY_BY_BUCKET` | `{"voice_call": 5.00}` | A live call's own monthly cap in US dollars, inside `MAX_SPEND_USD` and the daily and monthly limits (the tightest wins) |
| `VOICE_CALL_MAX_SECONDS` | `600` | The call ends at this much caller audio |

## The pieces

- `voice/gateway.py`: the rules (`propose`, `readback`, `guard_spoken`, `check_confirmation`). `TEXT_CHANNELS = ("app",)`.
- `voice/provider.py`: the provider interface (`open`, `listen`, `speak`, `close`), `NoProvider`, `FakeVoiceProvider` and `RealtimeVendorStub`. `choose()` reads `VOICE_PROVIDER`; an unknown name is `none`.
- `voice/shell.py`: `VoiceShell.handle(transcript)` is the one handler: transcript to the text agent, a proposal to the gateway, the read-back back to the caller.
- App, Approvals page: a voice-origin card shows "Heard on a call", the exact fields proposed, the sentence that was read back, and a button that is the only way to confirm.

## Try it without a vendor

```bash
cd backend
# the 30-task experiment: offline, fake model, free (needs a scratch database, see the evals docs)
EVAL_DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5432/agent_test_evals \
  uv run python -m superaide.evals voice-vs-text
```

It runs 30 tasks (18 plain, 12 with a mishearing scripted into the transcript) once as text and once through the shell with a `FakeVoiceProvider`, and prints how often voice diverged from text (dropped, spurious, wrong kind, wrong details), the wrong-action rate, the confirmation rate and that nothing ran without an app tap. The JSON goes to `backend/evals/results/voice-vs-text.json`. The text agent is a rule model, so the numbers show the mechanism, not a real model's accuracy: rerun it with a real model and real transcripts for that.

To see the card: with `VOICE_PROVIDER=fake` (or a key), call `voice.shell.VoiceShell(...).handle("tell Priya the meeting moved")` from a shell or a test, then open Approvals in the app.

## The hosted adapter: Deepgram

`VOICE_PROVIDER=deepgram` (or `auto` with a Deepgram key) uses `DeepgramVoiceProvider` (`voice/provider.py`). Deepgram transcribes each utterance the caller makes (`/v1/listen`, Nova-3) and speaks the text the shell hands back (`/v1/speak`, Aura-2). It is the same vendor and the same two endpoints the introduction call already uses (ADR 0012), so it adds no new holder of call audio. It is turn by turn: one request per utterance, no barge-in and no full-duplex streaming yet. The audio transport (a browser or a phone line) belongs to the caller: it passes `audio_in` (an iterable of utterances, for example cut by voice activity detection) and `audio_out(bytes)`.

- Transcripts only come out; `speak` sends exactly the text given; the session has no tools and no instructions to act; the key is used only in requests to `api.deepgram.com` and is never logged.
- Every request is reserved and settled through `BudgetGuard` (purposes `voice_call_asr` and `voice_call_tts`, prices in `voice/prices.py`). Those purposes form the bucket `voice_call`, capped per 30 days by `BUDGET_MONTHLY_BY_BUCKET` (US$5 by default). That cap sits inside `MAX_SPEND_USD`: the lifetime cap, the daily and monthly limits and the voice cap are all checked and the tightest refuses. When any of them (or the kill switch) refuses, the call ends cleanly (`ended_by == "budget"`); a call also ends at `VOICE_CALL_MAX_SECONDS`.
- An instance can tighten its own cap through `spend_limits.monthly_by_bucket`.
- The app is still the only second channel: a call through this adapter proposes into the same gateway, and a spoken yes, Telegram tap or Slack tap confirms nothing (`tests/voice/test_voice_deepgram.py`).
- Tests use a mock HTTP transport; nothing in the suite touches the network.

Not done: a live run against the real API (the key and the owner's go-ahead are needed) and the 30-task experiment on real speech.

## Adding another vendor

Copy `RealtimeVendorStub` in `voice/provider.py`, implement the four methods with the vendor's streaming API over `httpx` or a websocket (no SDK), and keep its rules: transcripts only (no audio, no vendor function calls, the session is created without tools), `speak` sends exactly the text it is given, the key goes only to the vendor and is never logged, and the vendor's price goes in `voice/prices.py` so the spend cap covers the call. Which vendor, and the cost, is the owner's decision.
