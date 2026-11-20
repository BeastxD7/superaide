# Voice notes and photos: pluggable providers

Issues #501 (voice notes) and #511 (photos and files). The owner decided: the transcription and vision services are a
user-configurable setting, and any provider can be used through one interface.

## What the owner sees

On the AI model page, below Models, the section "Voice notes and photos" has one row per capability. Each row picks one
of the services already connected on that page (Claude is offered for photos only) and a model, with **Save**,
**Test** and **Turn off**. Both capabilities start **off**: nothing is downloaded or sent to any service until the owner
chooses, because sending a person's voice or picture to a vendor is the owner's decision.

## Services

| Capability | Adapter | Works with |
| --- | --- | --- |
| Photos | `AnthropicVision` | Claude (the `anthropic` SDK, base64 image block) |
| Photos | `OpenAICompatVision` | OpenAI, OpenRouter, Gemini's compatibility endpoint, Ollama (llava, qwen-vl), LM Studio, vLLM |
| Voice notes | `OpenAICompatTranscriber` | OpenAI (`whisper-1`, `gpt-4o-transcribe`), a local Whisper server with an OpenAI-style `/audio/transcriptions` |
| both | `FakeVision`, `FakeTranscriber` | tests and offline demos (offered only while `FAKE_LLM` is on) |

A local server is connected like any other endpoint ("Other OpenAI-compatible service" or Ollama) and then chosen here.
The interfaces are `Transcriber` and `ImageDescriber` in `backend/src/superaide/brain/media/providers.py`; a new
service is one class plus a line in `service.build_adapter`.

## Money

Every call is reserved against the spend cap before it is made and settled from what the service reports
(`brain/media/service.py`). Photos are priced like text models (per million tokens: the built-in tables, the owner's
price, free on a local server). Voice notes are priced per audio minute (`brain/media/prices.py`); a model with no price
is refused until the owner sets one, and a server on this computer or the local network is free. Bytes bound the minutes
that can be billed (at least 8 kbit/s), so the reservation is a real upper bound, and the audio limit is 4 MB (1 MB for
strangers). Photos are bounded at 5 MB and 8,000 tokens each. "Test" makes one tiny real call through the same path.

Storage: the choice is a `model_role` row with the role `transcribe` or `vision`; the owner's voice-note price is an
`owner_model_price` row under the provider `stt:<endpoint key>` (the `input_usd_per_mtok` column read as US$ per
minute). No new table, so no migration.

## What the model may do with it

Code decides everything; the model never sees a stranger's file.

- **Known senders** (the owner and known contacts, in a direct message): the transcript or description enters the turn
  exactly like a PDF's text (`channels/attachments.py`): quoted data inside a block fenced by a random token, the turn
  is tainted (no action, mandate or auto-skip can rest on it), and the conversation log keeps only
  "[attachment read: voice]". It is never put in the message text, so it cannot become a reminder, a "remember" note or
  a link code.
- **Strangers**, only in receptionist mode and behind its gates (on, direct message, hourly and daily limits, storage
  cap) plus five files a day per sender: the file is read by code and stored for the owner as a labelled message
  ("Voice note from someone I don't know, transcribed by software"). No model turn, no claimed name, no linking. Their
  calls draw from the receptionist's budget bucket.
- Type comes from the file's first bytes, never its name or MIME type; the filename sent to a service is ours.

## Photos default to Claude

When Claude is connected and you have not picked a photo service, photos are read by Claude (`claude-haiku-5-5`). The settings page and the Check-my-setup list both say so, because each photo is sent to Anthropic. Turn it off once (the Turn off button, in either place) and it stays off until you choose a service. Voice notes have no default: they stay off until you configure a transcriber.

## Prices

Built-in transcription prices (OpenAI whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe) are list prices copied into the code. They are not checked live, so verify them against the vendor's current price page. The settings page labels them as list prices and lets you enter your own, which always wins.
