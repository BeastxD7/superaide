"""Every claim about the owner's own data must come from something Ava looked up this turn.

Two checks run on the owner-turn reply before it is sent, both in code:

- A cited `ava://` source must be one a tool actually returned this turn. Anything else is a
  fabricated citation and is dropped.
- A sentence that talks about the owner's notes, messages or plans ("your note from 21 Oct",
  "you said", "your meeting at 3pm") with no citation, in a turn where recall found nothing,
  is unsupported unless every date and time in it appears in what Ava saw this turn: the
  owner's own words in the conversation or a tool's output. That keeps "Done, I'll remind
  you at 5pm" and plain chit-chat untouched while catching a made-up "gym note from 21 Oct".

The loop (brain/loop.py) then asks the model once to remove or own up to what is
unsupported, and appends an honest line if it still doesn't.
"""

from __future__ import annotations

import json
import re
from typing import Any

ABSTAIN = "I don't have a source for that in your notes or messages, so please treat it as unconfirmed."

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WEEKDAYS = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"

# Dates and times a claim can be pinned to.
SPECIFIC = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}\b"  # 21 Oct, 21st of October
    rf"|\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b"  # Oct 21
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"  # 21/10
    r"|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"  # 7am, 7:10 pm
    r"|\b\d{1,2}:\d{2}\b"  # 14:30
    rf"|\b{_WEEKDAYS}\b",
    re.IGNORECASE,
)

# A sentence that says it comes from the owner's own records or words.
MEMORY_REF = re.compile(
    r"\byour\s+(?:\w+\s+)?(?:notes?|messages?|emails?|docs?|documents?|files?|records?|entr(?:y|ies)|logs?)\b"
    r"|\b(?:a|the)\s+(?:\w+\s+)?(?:note|message|email|entry)\s+(?:from|on|dated|about|saying)\b"
    r"|\bin\s+your\s+(?:notes|messages|records|calendar)\b"
    r"|\baccording\s+to\s+your\b"
    r"|\byou(?:'ve)?\s+(?:said|told\s+me|mentioned|wrote|noted|saved|shared|asked\s+me)\b",
    re.IGNORECASE,
)

# Second-person plans: only a claim when pinned to a date or time.
PLAN_REF = re.compile(
    r"\byour\s+(?:\w+\s+)?(?:meetings?|appointments?|flights?|plans?|schedule|calendar|calls?|events?|"
    r"sessions?|bookings?|reservations?|deadlines?|classes?|trips?)\b"
    r"|\byou(?:'re|\s+are|\s+have|'ve\s+got|\s+had|\s+were)\b",
    re.IGNORECASE,
)

# The sentence already owns up to having no source, or only points back at this chat.
HONEST = re.compile(
    r"\b(?:no|don't\s+have\s+a|do\s+not\s+have\s+a|couldn't\s+find\s+a|can't\s+find\s+a)\s+(?:source|record|note)"
    r"|\b(?:couldn't|can't|could\s+not|cannot|didn't|did\s+not)\s+find\b"
    r"|\bnothing\s+(?:in|matches|matched)\b"
    r"|\bjust\s+(?:said|told\s+me|mentioned)\b|\b(?:in|earlier\s+in)\s+this\s+(?:chat|conversation)\b",
    re.IGNORECASE,
)

# Words quoted back, as in: you said "call Priya".
_QUOTED = re.compile(r"[\"“]([^\"“”]{2,})[\"”]")

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*(?:[\"”’)]*)(?:\s*\[\d+\])*")
_MARK = re.compile(r"\[\d+\]")


def sentences(text: str) -> list[str]:
    """Sentences of a rendered reply, each keeping the [n] marks that follow it."""
    return [m.group(0).strip() for m in _SENTENCE.finditer(text) if m.group(0).strip(" -*\t")]


def returned_uris(content: Any) -> set[str]:
    """The ava:// sources in a tool result's content (recall, calendar and any other skill)."""
    if not isinstance(content, list):
        return set()
    return {
        str(b.get("source", ""))
        for b in content
        if isinstance(b, dict) and b.get("type") == "search_result" and b.get("source")
    }


def drop_unreturned(content: list[dict[str, Any]], allowed: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove citations of ava:// sources no tool returned this turn. Web citations come from
    the API's own server tools and are kept."""
    dropped: list[str] = []
    out: list[dict[str, Any]] = []
    for block in content:
        citations = block.get("citations") if block.get("type") == "text" else None
        if not citations:
            out.append(block)
            continue
        kept = []
        for c in citations:
            uri = str(c.get("source") or c.get("url") or "")
            if uri.startswith("ava://") and uri not in allowed:
                dropped.append(uri)
            else:
                kept.append(c)
        out.append({**block, "citations": kept} if len(kept) != len(citations) else block)
    return out, dropped


def grounding_text(messages: list[dict[str, Any]], content: list[dict[str, Any]]) -> str:
    """Everything Ava saw this turn that the owner can stand behind: their own words in the
    conversation, every tool result, and any web results in the final step."""
    parts: list[str] = []
    for msg in messages:
        body = msg.get("content")
        if msg.get("role") == "user":
            parts.append(body if isinstance(body, str) else json.dumps(body, default=str))
        elif isinstance(body, list):  # assistant steps: keep server tool results (web pages)
            parts.extend(
                json.dumps(b.get("content"), default=str)
                for b in body
                if isinstance(b, dict) and str(b.get("type", "")).endswith("_tool_result")
            )
    parts.extend(
        json.dumps(b.get("content"), default=str) for b in content if str(b.get("type", "")).endswith("_tool_result")
    )
    return flat("\n".join(parts))


_CLOCK = re.compile(r"(?<![:\d])(\d{1,2})\s?(am|pm)\b")
_MONTH_WORD = re.compile(rf"\b({_MONTHS})\b")


def flat(text: str) -> str:
    """Lower-case, spaces removed, "5pm" written "5:00pm" and months cut to three letters, so
    "Friday 5pm, 21 October" and "friday at 5:00 pm, 21 oct" compare equal piece by piece."""
    text = _CLOCK.sub(r"\1:00\2", text.lower())
    text = _MONTH_WORD.sub(lambda m: m.group(1)[:3], text)
    return re.sub(r"\s+", "", text)


def is_owner_claim(sentence: str) -> bool:
    """Shaped like a statement about the owner's own data (before checking its sources)."""
    if MEMORY_REF.search(sentence):
        return True
    return bool(PLAN_REF.search(sentence) and SPECIFIC.search(sentence))


def unsupported(text: str, *, grounding: str, recall_found: bool) -> list[str]:
    """Uncited sentences about the owner's data that nothing Ava saw this turn backs up."""
    if recall_found:  # recall found sources: an uncited paraphrase of them isn't policed here
        return []
    flagged = []
    for sentence in sentences(text):
        if _MARK.search(sentence) or not is_owner_claim(sentence) or HONEST.search(sentence):
            continue
        quoted = _QUOTED.findall(sentence)
        if quoted and all(flat(q) in grounding for q in quoted):
            continue  # it quotes the owner's own words back from this turn
        specifics = [m.group(0) for m in SPECIFIC.finditer(sentence)]
        if specifics and all(flat(s) in grounding for s in specifics):
            continue  # it repeats the owner's own words or a tool's output
        if not specifics and not MEMORY_REF.search(sentence):
            continue
        if not specifics and _echoes(sentence, grounding):
            continue  # "you told me on Telegram you're heading to the airport": their own words, from the timeline
        flagged.append(sentence)
    return flagged


_STOP = {
    "the", "and", "for", "that", "this", "with", "from", "your", "you", "are", "was", "has", "have", "his", "her",
    "their", "they", "not", "but", "its", "into", "about", "who", "what", "when", "where", "which",
}  # fmt: skip


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower().replace("’", "'")) if w not in _STOP}


def restates(sentence: str, source_text: str) -> bool:
    """The sentence says what the source says: three of the source's content words, or two that
    are most of a short note, appear in it. Chit-chat shares nothing with a note."""
    src = _content_words(source_text)
    common = src & _content_words(sentence)
    return len(common) >= 3 or (len(common) >= 2 and len(common) >= 0.6 * len(src))


def uncited_restatements(text: str, sources: list[str]) -> list[tuple[str, int]]:
    """Uncited sentences that restate one of this turn's sources, with that source's index."""
    out: list[tuple[str, int]] = []
    for sentence in sentences(text):
        if _MARK.search(sentence) or HONEST.search(sentence) or is_action_report(sentence) or is_reasoning(sentence):
            continue
        for i, src in enumerate(sources):
            if restates(sentence, src):
                out.append((sentence, i))
                break
    return out


# --- marks on sentences their source does not support (issue #341 D) ----------------------

# What Ava did ("I saved...", "queued for approval"): its evidence is the action record, never a note.
ACTION_REPORT = re.compile(
    r"\bI(?:'ve|\s+have|'ll|\s+will)?\s+(?:just\s+|already\s+|now\s+)?"
    r"(?:saved|sent|queued|set|created|scheduled|cancell?ed|moved|added|drafted|booked|deleted|updated|posted"
    r"|forwarded|replied|archived|declined|accepted|put|asked|submitted|filed)\b"
    r"|\b(?:sent|queued|waiting|saved)\s+(?:it\s+)?(?:to\s+your\s+dashboard|for\s+(?:your\s+)?approval)\b"
    r"|\bfor\s+your\s+approval\b|\bwon't\s+go\s+out\s+until\b|\bnothing\s+(?:has\s+)?(?:been\s+)?sent\b",
    re.IGNORECASE,
)

# Ava explaining her own choice or limits: not something a note says.
REASONING = re.compile(
    r"\bbecause\s+(?:I|my)\b|\b(?:so|and)\s+I\s+(?:used|chose|picked|went\s+with|decided|will|can)\b"
    r"|\bI\s+(?:used|chose|picked|went\s+with|decided|can't|cannot|couldn't|am\s+not\s+able|don't\s+have\s+access)\b"
    r"|\bI\s+(?:think|guess|assume|suppose)\b",
    re.IGNORECASE,
)

APPROVAL_PREFIX = "ava://approval/"


def is_action_report(sentence: str) -> bool:
    return bool(ACTION_REPORT.search(sentence))


def is_reasoning(sentence: str) -> bool:
    return bool(REASONING.search(sentence))


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _content_words(text)}


def mark_supported(sentence: str, uri: str, source_text: str) -> bool:
    """Whether a [n] on this sentence is fair to its source. Web sources (the API's own grounding) and
    action records pass; an action report or Ava's own reasoning does not take a note's mark; any
    other sentence needs to share two content words (or a date or time) with the source's text."""
    if not uri.startswith("ava://") or uri.startswith(APPROVAL_PREFIX):
        return True
    if is_action_report(sentence) or is_reasoning(sentence):
        return False
    if not source_text.strip():
        return True  # nothing to compare with: don't second-guess
    plain = _MARK.sub("", sentence)
    if len(_stems(plain) & _stems(source_text)) >= 2:
        return True
    hay = flat(source_text)
    specifics = [m.group(0) for m in SPECIFIC.finditer(plain)]
    return bool(specifics) and any(flat(sp) in hay for sp in specifics)


def prune_marks(text: str, sources: list[tuple[str, str]]) -> tuple[str, list[int], int]:
    """Drop [n] marks the source does not support (never ask the model to rewrite: no mark beats a wrong
    one). `sources` is (uri, text) per mark number. Returns the text with the remaining marks renumbered,
    the old indexes (0-based) of the sources still cited, and how many marks were dropped."""
    dropped = 0
    out = text
    for sentence in sentences(text):
        marks = _MARK.findall(sentence)
        if not marks:
            continue
        body = sentence
        for mark in dict.fromkeys(marks):
            i = int(mark[1:-1]) - 1
            if 0 <= i < len(sources) and not mark_supported(sentence, *sources[i]):
                body = re.sub(rf"[ \t]?{re.escape(mark)}", "", body)
                dropped += 1
        if body != sentence:
            out = out.replace(sentence, body, 1)
    if not dropped:
        return text, list(range(len(sources))), 0
    used = sorted({int(m[1:-1]) - 1 for m in _MARK.findall(out) if 0 < int(m[1:-1]) <= len(sources)})
    new_n = {old: k + 1 for k, old in enumerate(used)}
    out = _MARK.sub(
        lambda m: f"[{new_n[int(m.group(0)[1:-1]) - 1]}]" if int(m.group(0)[1:-1]) - 1 in new_n else m.group(0), out
    )
    return out, used, dropped


# Words that only say where something came from, not what it was.
_REF_WORDS = {
    "told", "tell", "said", "say", "mentioned", "wrote", "noted", "saved", "shared", "asked", "earlier", "just",
    "telegram", "slack", "app", "chat", "message", "messages", "note", "notes", "were", "was", "today", "now",
}  # fmt: skip


def _echoes(sentence: str, grounding: str) -> bool:
    """The sentence repeats what's in the grounding (the owner's own words this turn, including turns
    from their other channels): two or more of its content words, and most of them, are there."""
    words = _content_words(sentence) - _REF_WORDS
    found = [w for w in words if w in grounding]
    return len(found) >= 2 and len(found) >= 0.6 * len(words)


def revision_request(flagged: list[str]) -> str:
    lines = "\n".join(f"- {s}" for s in flagged)
    return (
        "[Source check by the app, not the owner] These sentences in your reply describe the owner's notes, "
        "messages or plans, but nothing you looked up this turn supports them:\n"
        f"{lines}\n"
        "Write your reply again without them. Where it matters, say plainly that you don't have a source "
        "for that. Keep everything else. Don't call tools and don't mention this check."
    )


def detail(dropped: int, flagged: int, outcome: str, pruned: int = 0) -> str:
    """The work-log line for the check."""
    bits = []
    if dropped:
        bits.append(f"Dropped {dropped} citation{'s' if dropped != 1 else ''} I couldn't find")
    if pruned:
        bits.append(f"Removed {pruned} mark{'s' if pruned != 1 else ''} the source didn't back")
    if flagged:
        bits.append(f"{flagged} claim{'s' if flagged != 1 else ''} had no source")
    if outcome == "revised":
        bits.append("rewrote the answer")
    elif outcome == "abstained":
        bits.append("said I have no source")
    return "; ".join(bits) if bits else "Every claim has a source"
