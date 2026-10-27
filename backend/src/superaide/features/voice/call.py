"""One voice call: its state, each turn as a stream of events, and the summary the owner confirms.

The call's state lives in the owner's onboarding row (`answers["voice_call"]`): the turns said so far,
the current topic, and the facts proposed at the end. Every line is also written to the in-app
conversation, like the rest of the introduction. Facts are saved only through `confirm`, with the
list the owner checked and edited.

A turn runs in its own thread and reports as it goes (Server-Sent Events, see `routes`):
    transcript  {text, asr_ms}                    what the owner said (server speech-to-text only)
    sentence    {i, text}                         the next thing Ava says, as soon as it's written
    audio       {i, mime, b64}                    that sentence spoken, when the server has a voice
    done        {ended, topic, timings, ...}      the turn is over
    error       {message}
"""

from __future__ import annotations

import base64
import contextvars
import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.onboarding import Onboarder, first_name
from superaide.features.voice import interview
from superaide.features.voice.providers import (
    ASR,
    TTS,
    VOICE_LABELS,
    VOICE_STYLES,
    Chosen,
    choose_asr,
    choose_tts,
    metered_speak,
    metered_transcribe,
    voice_id,
)
from superaide.memory.knowledge.store import CATEGORIES, remember_fact
from superaide.memory.store import log_event
from superaide.platform.audit import audit
from superaide.platform.db.models import Onboarding, Person
from superaide.platform.db.session import require, session_scope

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15
MAX_FACTS = 40
Emit = Callable[[str, dict[str, Any]], None]


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream(work: Callable[[Emit], None]) -> Iterator[str]:
    """Run `work` in its own thread (with the request's instance in scope) and relay its events.
    The work isn't tied to the connection: if the browser goes away, the turn is still recorded."""
    events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def run() -> None:
        try:
            work(lambda kind, data: events.put((kind, data)))
        except Exception:
            log.exception("voice turn failed")
            events.put(("error", {"message": "Something went wrong on my side. Please try again."}))
        finally:
            events.put(None)

    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(run,), name="voice-turn", daemon=True).start()
    yield ": open\n\n"
    while True:
        try:
            item = events.get(timeout=KEEPALIVE_SECONDS)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if item is None:
            return
        yield sse(*item)


@dataclass
class Turn:
    """What the browser sent for one turn: audio to transcribe, or text (browser speech or typed)."""

    audio: bytes | None = None
    mime: str = "audio/wav"
    text: str = ""
    heard: str | None = None  # the owner talked over Ava: the part of her last reply they heard
    lang: str = "en-US"
    received: float = field(default_factory=time.perf_counter)


class VoiceCall:
    def __init__(self, agent: Any, *, fakes: dict[str, Any] | None = None) -> None:
        self.agent = agent
        self.settings = agent.settings
        self.fakes = fakes  # tests: shared fake providers, so they can be inspected

    # --- state -------------------------------------------------------------------------------

    def _row(self, session: Session, person: Person, lock: bool = False) -> Onboarding:
        q = select(Onboarding).where(Onboarding.person_id == person.id)
        row = session.scalar(q.with_for_update() if lock else q)
        if row is None:
            row = Onboarder(self.agent, None).state(session, person)
        return row

    @staticmethod
    def _call(row: Onboarding) -> dict[str, Any]:
        return dict((row.answers or {}).get("voice_call") or {})

    @staticmethod
    def _save(row: Onboarding, call: dict[str, Any]) -> None:
        answers = dict(row.answers or {})
        answers["voice_call"] = call
        row.answers = answers  # a new dict, so the JSON column is written

    def style(self, row: Onboarding) -> str:
        s = (row.answers or {}).get("voice_style")
        return s if s in VOICE_STYLES else "formal_female"

    def providers(self, lang: str) -> tuple[Chosen[ASR], Chosen[TTS]]:
        return choose_asr(self.settings, fakes=self.fakes), choose_tts(self.settings, lang, fakes=self.fakes)

    def config(self, session: Session, person: Person, lang: str) -> dict[str, Any]:
        from superaide.brain.wiring import brain_ready
        from superaide.features.voice.prices import per_minute

        asr, tts = self.providers(lang)
        row = self._row(session, person)
        call = self._call(row)
        priced_asr = (asr.provider.name, asr.provider.model) if asr.provider else None
        priced_tts = (tts.provider.name, tts.provider.model) if tts.provider else None
        return {
            "enabled": True,
            "asr": asr.kind,
            "tts": tts.kind,
            "asr_paid_by": asr.key_source if asr.provider else None,
            "tts_paid_by": tts.key_source if tts.provider else None,
            "brain": "claude" if brain_ready(self.settings) else "scripted",
            "voice": self.style(row),
            "voices": [{"value": v, "label": VOICE_LABELS[v]} for v in VOICE_STYLES],
            "topics": len(interview.TOPICS),
            "max_turn_seconds": self.settings.voice_max_turn_seconds,
            "speech_usd_per_minute": float(per_minute(priced_asr, priced_tts)),
            "in_progress": bool(call.get("turns")) and not call.get("ended"),
            "proposed": call.get("proposed") if call.get("proposed") is not None else None,
        }

    def set_style(self, session: Session, person: Person, style: str) -> None:
        if style not in VOICE_STYLES:
            raise ValueError("unknown voice")
        row = self._row(session, person, lock=True)
        answers = dict(row.answers or {})
        answers["voice_style"] = style
        row.answers = answers

    # --- the conversation log ----------------------------------------------------------------

    def _log(self, session: Session, person: Person, direction: str, text: str) -> None:
        o = Onboarder(self.agent, None)
        if direction == "in":
            o._hear(session, person, text, via="call")
        else:
            _, ctx, aud = o._context(session, person)
            log_event(
                session, context=ctx, direction="out", content=text, audience=aud,
                meta={"onboarding": True, "via": "call"},
            )  # fmt: skip

    # --- turns -------------------------------------------------------------------------------

    def start(self, person_id: Any, lang: str) -> Callable[[Emit], None]:
        """A fresh call: Ava greets the owner and asks the first question."""
        received = time.perf_counter()

        def work(emit: Emit) -> None:
            with session_scope() as s:
                person = require(s, Person, person_id)
                row = self._row(s, person, lock=True)
                text = interview.greeting(first_name(person), self.settings.agent_name)
                call = {
                    "turns": [{"role": "ava", "text": text}],
                    "topic": 0,
                    "follow_ups": 0,
                    "seq": 0,
                    "ended": False,
                    "lang": lang[:20],
                    "started_at": datetime.now(UTC).isoformat(),
                }
                self._save(row, call)
                style = self.style(row)
                self._log(s, person, "out", text)
            _, tts = self.providers(lang)
            timings = self._speak(emit, list(interview.split_sentences(text)), tts, style, lang, received)
            emit("done", {"ended": False, "topic": 0, "timings": timings})

        return work

    def turn(self, person_id: Any, t: Turn) -> Callable[[Emit], None]:
        def work(emit: Emit) -> None:
            asr, tts = self.providers(t.lang)
            timings: dict[str, float] = {}
            said = " ".join(t.text.split())[:4000]
            if t.audio is not None:
                if asr.provider is None:
                    emit("error", {"message": "Speech-to-text isn't set up on the server; use your browser's."})
                    return
                guard = self._guard(asr.key_source)
                heard = metered_transcribe(
                    guard, asr.provider, t.audio, t.mime, t.lang, float(self.settings.voice_max_turn_seconds)
                )
                said = heard.text[:4000]
                timings["asr_ms"] = _ms(t.received)
                emit("transcript", {"text": said, "asr_ms": timings["asr_ms"]})
            if not said:
                emit("done", {"ended": False, "empty": True, "timings": timings})
                return

            # Record what the owner said (and, if they talked over Ava, what of hers they heard).
            with session_scope() as s:
                person = require(s, Person, person_id)
                name = person.display_name
                row = self._row(s, person, lock=True)
                call = self._call(row)
                if not call.get("turns") or call.get("ended"):
                    emit("error", {"message": "This call has ended. Start a new one to carry on."})
                    return
                turns = list(call["turns"])
                if t.heard is not None and turns and turns[-1]["role"] == "ava":
                    turns[-1] = {"role": "ava", "text": (t.heard.strip() + " …").strip(), "interrupted": True}
                turns.append({"role": "owner", "text": said})
                call["turns"] = turns
                call["seq"] = seq = int(call.get("seq", 0)) + 1
                topic, follow_ups = int(call.get("topic", 0)), int(call.get("follow_ups", 0))
                style = self.style(row)
                self._save(row, call)
                self._log(s, person, "in", said)

            # Decide and say the reply, sentence by sentence.
            sentences: queue.Queue[str | None] = queue.Queue()
            speaker = threading.Thread(
                target=contextvars.copy_context().run,
                args=(self._speak_queue, emit, sentences, tts, style, t.lang, t.received, timings),
                name="voice-tts",
                daemon=True,
            )
            speaker.start()
            tag, reply = self._reply(turns, name, topic, follow_ups, t.lang, sentences.put)
            ended = tag == "end" or (tag == "next" and topic >= len(interview.TOPICS) - 1)
            if ended and tag != "end":
                extra = interview.closing(first_name_of(name))
                for sentence in interview.split_sentences(extra):
                    sentences.put(sentence)
                reply = f"{reply} {extra}".strip()
            if tag == "end" and not reply:
                reply = interview.closing(first_name_of(name))
                for sentence in interview.split_sentences(reply):
                    sentences.put(sentence)
            sentences.put(None)

            # Save the reply as soon as it's decided, before the voice finishes.
            with session_scope() as s:
                person = require(s, Person, person_id)
                row = self._row(s, person, lock=True)
                call = self._call(row)
                if tag == "follow":
                    call["follow_ups"] = follow_ups + 1
                else:
                    call["topic"], call["follow_ups"] = min(topic + 1, len(interview.TOPICS)), 0
                call["ended"] = ended
                if int(call.get("seq", 0)) == seq:  # not already talked over by a newer turn
                    call["turns"] = list(call.get("turns", [])) + [{"role": "ava", "text": reply}]
                self._save(row, call)
                self._log(s, person, "out", reply)
                new_topic = call["topic"]
            speaker.join()
            timings["total_ms"] = _ms(t.received)
            emit("done", {"ended": ended, "topic": new_topic, "timings": timings})

        return work

    def _guard(self, key_source: str) -> Any:
        from superaide.brain.spend.budget import BudgetGuard

        return BudgetGuard.for_instance(self.settings, key_source=key_source)

    def _reply(
        self, turns: list[dict[str, Any]], name: str, topic: int, follow_ups: int, lang: str, say: Callable[[str], None]
    ) -> tuple[str, str]:
        """(tag, words). Claude when there's a brain; the scripted interview otherwise or if it fails."""
        from superaide.brain.wiring import brain_ready

        if brain_ready(self.settings):
            reader = interview.ReplyReader(say)
            try:
                self.agent.llm.step(
                    purpose="voice_turn",
                    model=self.settings.model_chat,
                    system=interview.system_prompt(
                        agent=self.settings.agent_name, name=name, lang=lang, topic=topic, follow_ups=follow_ups
                    ),
                    messages=as_messages(turns),
                    tools=[],
                    max_tokens=300,
                    on_event=lambda kind, data: reader.feed(data.get("delta", "")) if kind == "text" else None,
                )
                reader.close()
                if reader.text or reader.tag == "end":
                    tag = reader.tag or "next"
                    if tag == "follow" and follow_ups >= interview.MAX_FOLLOW_UPS:
                        tag = "next"  # one follow-up per topic: code, not the model, enforces it
                    return tag, reader.text
            except Exception as exc:  # spend cap, kill switch, provider error: carry on scripted
                log.warning("voice turn fell back to the script: %s", type(exc).__name__)
                if reader.text:
                    reader.close()
                    return reader.tag or "next", reader.text
        owner_turns = sum(1 for x in turns if x["role"] == "owner")
        tag, words = interview.scripted_reply(turns[-1]["text"], topic, owner_turns)
        for sentence in interview.split_sentences(words):
            say(sentence)
        return tag, words

    def _speak_queue(
        self,
        emit: Emit,
        sentences: queue.Queue[str | None],
        tts: Chosen[TTS],
        style: str,
        lang: str,
        received: float,
        timings: dict[str, float],
    ) -> None:
        i = 0
        while (sentence := sentences.get()) is not None:
            timings.update(self._speak(emit, [sentence], tts, style, lang, received, start=i, first=i == 0))
            i += 1

    def _speak(
        self,
        emit: Emit,
        sentences: list[str],
        tts: Chosen[TTS],
        style: str,
        lang: str,
        received: float,
        *,
        start: int = 0,
        first: bool = True,
    ) -> dict[str, float]:
        timings: dict[str, float] = {}
        for n, sentence in enumerate(sentences):
            i = start + n
            if first and n == 0:
                timings["first_text_ms"] = _ms(received)
            emit("sentence", {"i": i, "text": sentence})
            if tts.provider is None:
                continue
            try:
                audio = metered_speak(
                    self._guard(tts.key_source), tts.provider, sentence, voice_id(tts.kind, style), lang
                )
            except Exception as exc:  # the browser's own voice says it instead
                log.warning("voice failed: %s", type(exc).__name__)
                emit("audio", {"i": i, "mime": "", "b64": "", "fallback": True})
                continue
            if first and n == 0:
                timings["first_audio_ms"] = _ms(received)
            emit("audio", {"i": i, "mime": tts.provider.mime, "b64": base64.b64encode(audio).decode()})
        return timings

    # --- the end: what I learned -------------------------------------------------------------

    def finish(self, session: Session, person: Person) -> dict[str, Any]:
        """Read the call for facts and propose them. Nothing is saved until the owner confirms."""
        from superaide.memory.knowledge.facts import extract_facts

        row = self._row(session, person, lock=True)
        call = self._call(row)
        turns = list(call.get("turns") or [])
        answers = [x["text"] for x in turns if x["role"] == "owner"]
        if not answers:
            call.update({"ended": True, "proposed": []})
            self._save(row, call)
            return {"facts": [], "summary": "We didn't get to talk, so there's nothing to save."}
        name = person.display_name
        transcript = "\n".join(f"{'Ava' if x['role'] == 'ava' else name}: {x['text']}" for x in turns)
        found = extract_facts(self.agent, owner_name=name, text=transcript, source="the introduction voice call")
        if found:
            facts = [{"content": f.content, "category": f.category} for f in found]
        else:  # offline, or reading failed: keep each answer in their own words
            facts = [{"content": f"On the intro call, {name} said: {a}", "category": "about"} for a in answers]
        facts = [{"id": str(i), **f} for i, f in enumerate(facts[:MAX_FACTS])]
        call.update({"ended": True, "proposed": facts})
        self._save(row, call)
        n = len(facts)
        return {
            "facts": facts,
            "summary": f"Here's what I learned: {n} {'thing' if n == 1 else 'things'}. "
            "Check them, change or remove anything, then save.",
        }

    def confirm(self, session: Session, person: Person, facts: list[dict[str, str]]) -> dict[str, Any]:
        """Save the facts the owner checked (owner-only, deduplicated, source "intro_call")."""
        row = self._row(session, person, lock=True)
        call = self._call(row)
        saved = 0
        for f in facts[:MAX_FACTS]:
            content = " ".join(str(f.get("content", "")).split())[:500]
            category = str(f.get("category", "about"))
            if not content:
                continue
            item = remember_fact(
                session, owner=person, content=content, category=category if category in CATEGORIES else "about",
                source="intro_call",
            )  # fmt: skip
            saved += item is not None
        call.update({"proposed": None, "confirmed_at": datetime.now(UTC).isoformat(), "saved": saved})
        self._save(row, call)
        audit(session, "app", "voice_intro_confirmed", person_id=person.id, saved=saved, offered=len(facts))
        line = (
            f"Thanks for the call. I've saved {saved} {'thing' if saved == 1 else 'things'} about you to my notebook."
            if saved
            else "Thanks for the call. I haven't saved anything from it."
        )
        Onboarder(self.agent, None)._say(session, person, [line])
        return {"saved": saved, "message": line}


def as_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The call so far as alternating user/assistant messages, starting with the owner."""
    out: list[dict[str, Any]] = [{"role": "user", "content": "(The call has started.)"}]
    for x in turns:
        role = "assistant" if x["role"] == "ava" else "user"
        text = x["text"] + (" [they interrupted here]" if x.get("interrupted") else "")
        if out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    return out


def first_name_of(name: str) -> str:
    return name.split()[0] if name.strip() else "there"


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)
