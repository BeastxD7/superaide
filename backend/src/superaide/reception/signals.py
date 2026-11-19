# ruff: noqa: E501  (the word lists are long regular expressions)
"""Safety signals in a stranger's message (#498): distress, threat, harassment, an authority claim, a request for
credentials, private details or money, and floods.

Everything here is decided in code, from the stranger's own words, and never changes who they are: a signal cannot
make anyone a colleague, a manager or trusted, and it never reaches a model prompt (the receptionist model is not
told what code detected, so a stranger cannot steer it either way). The result is a set of fixed labels. Only the
labels and counts are stored (in `reception_message.rule_hits` and the audit log), never a quote.

Each label picks a fixed, reviewed reply in `service.py`. The rules are deliberately broad: a false alarm costs the
owner one extra look, a miss costs a person in distress a scripted answer, or leaks data to a scam.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

DISTRESS = "distress"
THREAT = "threat"
HARASSMENT = "harassment"
AUTHORITY = "authority_claim"
CREDENTIAL = "credential_request"
PAYMENT = "payment_request"
PRIVATE = "private_request"
FLOOD = "flood"

LABELS = (DISTRESS, THREAT, HARASSMENT, AUTHORITY, CREDENTIAL, PAYMENT, PRIVATE, FLOOD)
SEVERE = (DISTRESS, THREAT)  # the owner is told at once, whatever the hourly note cap
ASKS = (CREDENTIAL, PAYMENT)  # always refused with the one fixed sentence
PREFIX = "signal:"


def _fold(text: str) -> str:
    """Lower case, accents removed, apostrophes and spacing normalised, so "can’t" and "CAN'T" read the same."""
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("`", "'").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


# English first; Spanish, French and romanised Hindi for the most common phrases (the list grows with reports).
_DISTRESS = _any(
    r"\b(?:can'?t|cannot|can not|couldn'?t) (?:do|take|handle|go on|carry on|keep going|live) (?:with )?(?:this|like this|any ?more)",
    r"\bcan'?t (?:do|take|go on|keep going)\b.{0,20}\bany ?more\b",
    r"\b(?:want|wanna|going|plan(?:ning)?|ready) to (?:die|end it|end my life|kill myself|disappear)\b",
    r"\bkill(?:ing)? myself\b|\bsuicid|\bself[- ]?harm|\bhurt(?:ing)? myself\b|\bend(?:ing)? (?:it all|my life)\b",
    r"\bno (?:reason|point) (?:to|in) (?:live|living|go on|going on)\b",
    r"\b(?:better off|world is better) without me\b|\bdon'?t want to (?:be here|be alive|live|wake up)\b",
    r"\bgive up on (?:life|everything)\b",
    r"\bquiero morir|\bme quiero matar|\bsuicidarme|\bno puedo mas\b|\bno quiero vivir",
    r"\bje veux mourir|\bme suicider|\bj'en peux plus\b|\bje n'en peux plus\b",
    r"\bmarna chahta|\bmarna chahti|\bjeena nahi|\bjeene ka mann nahi|\bkhudkushi|\bmar jaana chahta|\bmar jana chahta",
)
_THREAT = _any(
    r"\bi(?:'ll| will|'m going to| am going to|'m gonna) (?:kill|hurt|find|destroy|ruin|burn|shoot|stab|beat) (?:you|him|her|them)\b",
    r"\b(?:you|he|she|they)(?:'ll| will) (?:regret|pay for|be sorry)\b",
    r"\bi know where (?:you|he|she|they) (?:live|work|stay)s?\b",
    r"\bwatch your back\b|\bbomb\b|\bi(?:'ll| will) (?:come|be) (?:for|after) (?:you|him|her)\b",
    r"\bte voy a matar|\bje vais te tuer|\bmain tujhe maar|\bmaar dunga|\bmar dunga tujhe",
)
_HARASS = _any(
    r"\b(?:fuck|screw|piss) (?:you|off)\b|\bbitch\b|\bbastard\b|\basshole\b|\bmotherfucker\b|\bscumbag\b|\bshut (?:the f\w+ )?up\b",
    r"\b(?:you(?:'re| are)|ur) (?:a )?(?:stupid|useless|worthless|pathetic|idiot|moron|slut)\b",
    r"\bchutiya|\bmadarchod|\bbehenchod|\bharamzada|\bgaandu\b|\bputa\b|\bcabron|\bconnard|\bsalope",
    r"\bsend (?:me )?(?:nudes|pics of yourself)\b|\bsexy\b.{0,20}\b(?:baby|girl)\b",
)
_AUTHORITY = _any(
    r"\b(?:your|the|his|her) (?:boss|manager|ceo|director|owner|employer|supervisor|principal|client|lawyer|attorney)\b"
    r".{0,40}\b(?:told|asked|said|sent|wants|instructed|authori[sz]ed)\b",
    r"\b(?:he|she|they|\w+) (?:asked|told|sent|instructed|authori[sz]ed|said to|wants) (?:me|us) to\b",
    r"\b(?:he|she|they|\w+) (?:said|told me|says) (?:you|ava|the assistant)(?: can| could| should| will| would|'ll)\b",
    r"\bon behalf of\b|\bacting for\b|\bper (?:his|her|their) (?:instruction|request)s?\b|\bhe (?:has )?(?:approved|authori[sz]ed)\b",
    r"\bi(?:'m| am) (?:his|her|the) (?:assistant|lawyer|attorney|accountant|driver|courier|friend|brother|sister|wife|husband|colleague|doctor|nurse|banker|agent)\b",
    r"\bi(?:'m| am) (?:calling|writing|messaging|from) (?:from|on behalf of|for) (?:the )?(?:bank|police|tax|irs|hmrc|court|customs|immigration|hospital|your (?:boss|company|office))\b",
    r"\b(?:official|urgent|authori[sz]ed) (?:request|order|instruction)\b",
    r"\bme (?:pidio|dijo) que\b|\bil m'a (?:demande|dit) de\b|\bunhone(?:n)? (?:kaha|bola) (?:ki )?(?:mujhe|tum)\b|\bsir ne (?:kaha|bola)\b",
)
_CREDENTIAL = _any(
    r"\b(?:otp|one[- ]time (?:code|pass(?:word|code)?)|verification code|security code|auth(?:entication)? code|2fa|mfa)\b",
    r"\b(?:pass ?(?:word|code)|pin(?: number| code)?|cvv|cvc|api[- ]?key|secret key|private key|seed phrase|recovery phrase)\b",
    r"\b(?:door|gate|alarm|entry|access|lock|garage|building|wifi|wi-fi|safe|locker) ?(?:code|pin|combination|password|key)\b",
    r"\b(?:login|log in|sign[- ]?in) (?:details|credentials|info(?:rmation)?)\b|\bcredentials\b",
    r"\b(?:card|account|credit card|debit card|aadhaar|aadhar|social security|ssn|passport) (?:number|no\.?|details|digits)\b",
    r"\b(?:contrasena|clave|mot de passe)\b",
)
_PAYMENT = _any(
    r"\b(?:send|transfer|wire|pay|give|lend|loan|remit|venmo|zelle|paypal|cashapp|cash app)\b.{0,25}"
    r"(?:\$|usd|eur|inr|rs\.?|rupees?|dollars?|euros?|pounds?|money|cash|funds|payment|upi|crypto|bitcoin|btc|usdt|eth)\b",
    r"\b(?:gift ?cards?|itunes card|google play card|steam card|western union|moneygram|bitcoin|usdt|crypto wallet)\b",
    r"\b(?:bank|wire|account|upi|payment|iban|swift|routing) (?:details|info(?:rmation)?|transfer|id|number)\b",
    r"\b(?:unpaid|overdue|outstanding) (?:invoice|fine|tax|bill|balance|fee)\b|\bprocessing fee\b|\bclaim your (?:prize|refund|reward)\b",
    r"[\$₹€£]\s?\d{3,}|\b\d{3,}\s?(?:usd|eur|inr|rupees|dollars)\b",
)
_PRIVATE = _any(
    r"\b(?:home|house|residential|current|exact|his|her|their) (?:address|location|whereabouts)\b",
    r"\bwhere (?:does|is|do|will|can i find) (?:he|she|they|your boss|the boss|[a-z]+) (?:live|stay|living|staying|now|right now|today|be)\b",
    r"\b(?:his|her|their|personal|private|cell|mobile|home) (?:phone|number|mobile|cell|email|e-mail|whatsapp)\b",
    r"\b(?:his|her|their|the boss'?s?) (?:schedule|calendar|itinerary|flight|travel plans|routine|whereabouts)\b",
    r"\b(?:what|which) (?:time|hotel|flight|car|route)\b.{0,30}\b(?:he|she|they|boss)\b",
    r"\bgive me (?:his|her|their|the) (?:address|number|details|info)\b|\bdireccion de\b",
)


@dataclass(frozen=True)
class Signals:
    labels: tuple[str, ...] = ()

    def has(self, *names: str) -> bool:
        return any(n in self.labels for n in names)

    def __bool__(self) -> bool:
        return bool(self.labels)

    @property
    def wants_data(self) -> bool:
        """Credentials or money, or private details asked for in someone else's name: all refused alike."""
        return self.has(*ASKS) or (self.has(AUTHORITY) and self.has(PRIVATE))

    def with_(self, *extra: str) -> Signals:
        return Signals(tuple(dict.fromkeys((*self.labels, *extra))))

    def hits(self) -> list[str]:
        return [PREFIX + label for label in self.labels]


NONE = Signals()


def detect(text: str) -> Signals:
    """The labels that apply to `text`, in a fixed order. Never raises, never looks anything up."""
    t = _fold(text or "")
    found = [
        label
        for label, rx in (
            (DISTRESS, _DISTRESS),
            (THREAT, _THREAT),
            (HARASSMENT, _HARASS),
            (AUTHORITY, _AUTHORITY),
            (CREDENTIAL, _CREDENTIAL),
            (PAYMENT, _PAYMENT),
            (PRIVATE, _PRIVATE),
        )
        if rx.search(t)
    ]
    return Signals(tuple(found))


def from_hits(hits: list[str] | None) -> tuple[str, ...]:
    return tuple(h[len(PREFIX) :] for h in hits or [] if h.startswith(PREFIX))
