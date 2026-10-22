"""The introduction's voice call, v2 (ADR 0012).

The browser listens with voice activity detection (Silero VAD, on the device) and sends one
utterance at a time; the server turns it into text (`providers`), lets Claude take the next turn of
the interview (`interview`), and streams the reply back sentence by sentence with a natural voice
(`call`). Facts heard on the call are only saved once the owner confirms the summary.

Audio is never logged or stored: it lives in memory for the length of one request.
"""
