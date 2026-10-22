"""What Ava asks on the voice call, and how one turn of the interview is decided (ADR 0012).

The plan is fixed in code: six topics, one question at a time, at most one follow-up per topic,
and every topic can be skipped. Claude only writes what Ava says next, grounded in what the owner
just said; code decides which topic is current and when the call ends. Each reply starts with a
tag the code reads and strips before anything is spoken:

    [follow] a follow-up on the current topic
    [next]   the current topic is done (or skipped); the reply moves on to the next one
    [end]    the last topic is done; the reply thanks them and closes

Without a model (offline, or the spend cap reached) a scripted interview runs instead, so the
call still works and still learns.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from superaide.brain.providers.types import SystemBlock


@dataclass(frozen=True)
class Topic:
    key: str
    goal: str  # what Ava wants to understand, for the model
    question: str  # the scripted question, used offline and as the model's example


TOPICS: list[Topic] = [
    Topic(
        "you", "who they are and what they do", "To begin, could you tell me a little about yourself and what you do?"
    ),
    Topic(
        "work",
        "what their work looks like right now, what they're focused on, and when they're hardest to reach",
        "What are you focused on at the moment, and when are you usually hardest to reach?",
    ),
    Topic(
        "people",
        "the people who matter most (family, team, clients, investors) and who should always get through",
        "Who are the people who matter most to you, and who should always be able to get through?",
    ),
    Topic(
        "reach",
        "how to reach them when something is urgent, and what counts as urgent",
        "When something genuinely can't wait, how should I reach you, and what counts as urgent?",
    ),
    Topic(
        "quiet",
        "their quiet hours and any times they never want to be disturbed",
        "Are there hours when you'd rather not be disturbed at all?",
    ),
    Topic(
        "share",
        "what Ava may tell people who ask about them, and anything she must never share",
        "Lastly, when people ask about you, what may I tell them, and is there anything I should never share?",
    ),
]
MAX_FOLLOW_UPS = 1
SKIP = re.compile(r"\b(skip|pass|rather not|prefer not|next question|move on|no comment)\b", re.I)
ACKS = ["Thank you.", "That's helpful, thank you.", "Understood.", "Noted, thank you.", "Thank you, that's clear."]


def greeting(first_name: str, agent_name: str) -> str:
    return (
        f"Hello {first_name}, I'm {agent_name}. Thank you for making the time. I'd like to learn a little "
        "about you, so I can look after people the way you would. It takes about five minutes, and you can "
        f"skip anything you like. {TOPICS[0].question}"
    )


def closing(first_name: str) -> str:
    return (
        f"Thank you, {first_name}, that's everything I need for now. I'll show you what I've learned, "
        "so you can check it before I save anything."
    )


SYSTEM = """You are {agent}, a personal assistant who has just been hired by {name}. This is your first \
conversation: a short voice call to get to know them, the way a thoughtful new executive assistant would \
on their first day. You are an AI and never pretend otherwise.

How you speak:
- Warm, composed and professional. Plain spoken English (or the owner's language: {lang}), no lists, no \
markdown, no emoji. Your words are read aloud.
- Short: one or two sentences to acknowledge, then exactly ONE question. Never ask two questions at once.
- Ground every reply in what they just said. Pick out a specific detail, not a generic "great".
- Don't repeat back everything. Don't flatter. Don't give advice. Don't promise to do anything yet.
- If they want to skip, or don't want to answer, accept it graciously and move on.
- If they ask you something, answer in a sentence, then carry on.

The interview plan (code keeps track; you follow it):
{plan}

Right now: {now}

Begin your reply with exactly one tag, then the words you'll say:
[follow] to ask one follow-up about the current topic (only if their answer was thin or raised something \
worth understanding){follow_note}
[next] to move on: briefly acknowledge, then ask about the next topic: {next_goal}
[end] only when the last topic is done: thank them warmly in one or two sentences and say you'll show them \
what you've learned to check before anything is saved. No question."""


def plan_text(topic: int) -> str:
    lines = []
    for i, t in enumerate(TOPICS):
        mark = "done" if i < topic else ("CURRENT" if i == topic else "later")
        lines.append(f"{i + 1}. {t.goal} ({mark})")
    return "\n".join(lines)


def system_prompt(*, agent: str, name: str, lang: str, topic: int, follow_ups: int) -> list[SystemBlock]:
    last = topic >= len(TOPICS) - 1
    nxt = "(there is no next topic: use [end] instead)" if last else TOPICS[topic + 1].goal
    now = f"topic {topic + 1} of {len(TOPICS)}: {TOPICS[min(topic, len(TOPICS) - 1)].goal}."
    follow_note = (
        " You've already asked a follow-up here, so do not use [follow] now." if follow_ups >= MAX_FOLLOW_UPS else ""
    )
    text = SYSTEM.format(
        agent=agent, name=name, lang=lang, plan=plan_text(topic), now=now, next_goal=nxt, follow_note=follow_note
    )
    return [SystemBlock(text)]


# --- reading the model's reply as it streams -----------------------------------------------------

TAG = re.compile(r"^\s*\[(follow|next|end)\]\s*", re.I)
SENTENCE_END = re.compile(r"([.!?…]+[\"')\]]*)(\s+)")


class ReplyReader:
    """Reads streamed text: the leading tag first, then whole sentences as they complete.
    Short sentences are joined to the next, so each spoken chunk sounds natural."""

    def __init__(self, on_sentence: Callable[[str], None], min_chars: int = 24) -> None:
        self.on_sentence = on_sentence
        self.min_chars = min_chars
        self.tag: str | None = None
        self._head = ""  # text before the tag has been read
        self._buf = ""
        self.text = ""  # everything said, without the tag

    def feed(self, delta: str) -> None:
        if self.tag is None:
            self._head += delta
            m = TAG.match(self._head)
            if m:
                self.tag = m.group(1).lower()
                rest = self._head[m.end() :]
            elif len(self._head) > 12 or (self._head.strip() and not "[".startswith(self._head.strip()[:1])):
                self.tag = "next"  # no tag: treat it as moving on
                rest = self._head
            else:
                return
            self._head = ""
            delta = rest
        self._buf += delta
        self.text += delta
        self._flush(final=False)

    def _flush(self, final: bool) -> None:
        while True:
            m = None
            for cand in SENTENCE_END.finditer(self._buf):
                if cand.end(1) >= self.min_chars:
                    m = cand
                    break
            if m is None:
                break
            sentence, self._buf = self._buf[: m.end(1)], self._buf[m.end() :]
            if sentence.strip():
                self.on_sentence(" ".join(sentence.split()))
        if final and self._buf.strip():
            self.on_sentence(" ".join(self._buf.split()))
            self._buf = ""

    def close(self) -> None:
        if self.tag is None and self._head:
            head, self._head = self._head, ""
            self.tag = "next"
            self._buf += head
            self.text += head
        self._flush(final=True)
        self.text = " ".join(self.text.split())


def split_sentences(text: str) -> Iterator[str]:
    out: list[str] = []
    reader = ReplyReader(out.append)
    reader.tag = "next"
    reader.feed(text)
    reader.close()
    yield from out


# --- the scripted interview (no model) ----------------------------------------------------------


def scripted_reply(answer: str, topic: int, turn: int) -> tuple[str, str]:
    """(tag, words) without a model: acknowledge, then the next scripted question."""
    ack = "No problem, we'll leave that." if SKIP.search(answer) else ACKS[turn % len(ACKS)]
    if topic >= len(TOPICS) - 1:
        return "end", ""
    return "next", f"{ack} {TOPICS[topic + 1].question}"
