# Two-channel voice (#125)

Live voice is a thin shell. It turns speech into text, hands the text to the usual agent, and speaks back what it is given. It holds no tools, no memory and no policy. Anything the agent proposes from a call becomes an ordinary approval marked `origin: voice`, and **only a tap inside the Superaide app confirms it**. A spoken yes, a Telegram tap and a Slack tap are all refused (fail closed, tested in `backend/tests/test_voice_provider.py`). A "no" is accepted from anywhere. The call never says "done" about something that is still waiting: such a reply is replaced by the read-back, which says nothing has happened.

Everything is off by default. Nothing is bought.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `VOICE_TWO_CHANNEL` | `false` | A voice-call action becomes an approval that only an app tap can approve |
| `VOICE_PROVIDER` | `none` | `none`, `fake` (scripted, offline) or `realtime` (adapter stub, no vendor wired) |
| `VOICE_PROVIDER_API_KEY` | empty | The vendor's key. Normally saved in the vault under the `voice_provider` connection |

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

To see the card: with `VOICE_TWO_CHANNEL=true`, call `voice.shell.VoiceShell(...).handle("tell Priya the meeting moved")` from a shell or a test, then open Approvals in the app.

## Adding a real vendor

Copy `RealtimeVendorStub` in `voice/provider.py`, implement the four methods with the vendor's streaming API over `httpx` or a websocket (no SDK), and keep its rules: transcripts only (no audio, no vendor function calls, the session is created without tools), `speak` sends exactly the text it is given, the key goes only to the vendor and is never logged, and the vendor's price goes in `voice/prices.py` so the spend cap covers the call. Which vendor, and the cost, is the owner's decision.
