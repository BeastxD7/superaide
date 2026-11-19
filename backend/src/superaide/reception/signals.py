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
    # "I can't do this anymore": the "anymore" (or "go on" at the end of a clause) is what separates it from
    # "I can't do this Friday" and "I can't take this call".
    r"\bcan'?t (?:do|take|handle|stand|bear|live)(?: this| it| that| life)?(?: like this)? any ?more\b",
    r"\bcan'?t (?:go on|carry on|keep going)(?: like this| living)?(?: any ?more)?(?:[.!?,;]|$)",
    r"\bi (?:really |just |only )?(?:want|wanna|need) to (?:die|end it|end my life|kill myself|disappear)\b",
    r"\bi(?:'m| am|'ll| will) (?:going to|gonna|about to) (?:die|kill myself|end it|end my life)\b",
    r"\bkill(?:ing)? myself\b|\bsuicid|\bself[- ]?harm|\bhurt(?:ing)? myself\b|\bend(?:ing)? (?:it all|my life)\b",
    r"\bno (?:reason|point) (?:to|in) (?:live|living|go on|going on)\b",
    r"\b(?:better off|world is better) without me\b|\bdon'?t want to (?:be here|be alive|live|wake up)\b",
    r"\bgive up on (?:life|everything)\b",
    r"\bquiero morir|\bme quiero matar|\bsuicidarme|\bno puedo mas\b|\bno quiero vivir|\bya no quiero vivir",
    r"\bje veux mourir|\bme suicider|\bj'en peux plus\b|\bje n'en peux plus\b",
    r"\bmarna chahta|\bmarna chahti|\bjeena nahi|\bjeene ka mann nahi|\bkhudkushi|\bmar jaana chahta|\bmar jana chahta",
)
_THREAT = _any(
    r"\bi(?:'ll| will|'m going to| am going to|'m gonna) (?:kill|hurt|destroy|ruin|burn|shoot|stab|beat) (?:you|him|her|them)\b",
    r"\bi(?:'ll| will|'m going to| am going to|'m gonna) find you and\b",
    r"\b(?:you|he|she|they)(?:'ll| will) (?:regret|be sorry)\b",
    r"\bi know where you (?:live|work|stay)s?\b",
    r"\bwatch your back\b|\bbomb threat\b|\b(?:there(?:'s| is) a|i(?:'ve| have) a|plant(?:ed|ing)? a) bomb\b",
    r"\bte voy a matar|\bje vais te tuer|\bmain tujhe maar|\bmaar dunga|\bmar dunga tujhe",
)
_HARASS = _any(
    r"\b(?:fuck|screw|piss) (?:you|off)\b|\bbitch\b|\bbastard\b|\basshole\b|\bmotherfucker\b|\bscumbag\b|\bshut (?:the f\w+ )?up\b",
    r"\b(?:you(?:'re| are)|ur) (?:a )?(?:stupid|useless|worthless|pathetic|idiot|moron|slut)\b",
    r"\bchutiya|\bmadarchod|\bbehenchod|\bharamzada|\bgaandu\b|\bputa\b|\bcabron|\bconnard|\bsalope",
    r"\bsend (?:me )?(?:nudes|pics of yourself)\b",
)
_AUTHORITY = _any(
    r"\b(?:your|the|his|her) (?:boss|manager|ceo|director|owner|employer|supervisor|principal|client|lawyer|attorney)\b"
    r".{0,40}\b(?:told|asked|said|sent|wants|instructed|authori[sz]ed)\b",
    r"\b(?:he|she|they|\w+) (?:asked|told|sent|instructed|authori[sz]ed|said to|wants) (?:me|us) to\b",
    r"\b(?:he|she|they|\w+) (?:said|told me|says) (?:you|ava|the assistant)(?: can| could| should| will| would|'ll)\b",
    r"\bon behalf of\b|\bacting for\b|\bper (?:his|her|their) (?:instruction|request)s?\b|\bhe (?:has )?(?:approved|authori[sz]ed)\b",
    r"\bi(?:'m| am) (?:his|her|the) (?:assistant|lawyer|attorney|accountant|brother|sister|wife|husband|doctor|nurse|banker|agent)\b",
    r"\bi(?:'m| am) (?:calling|writing|messaging|from) (?:from|on behalf of|for) (?:the )?(?:bank|police|tax|irs|hmrc|court|customs|immigration|hospital|your (?:boss|company|office))\b",
    r"\bme (?:pidio|dijo) que\b|\bil m'a (?:demande|dit) de\b|\bunhone(?:n)? (?:kaha|bola) (?:ki )?(?:mujhe|tum)\b|\bsir ne (?:kaha|bola)\b",
)
# A request is a verb that asks Ava to hand something over, within a few words of the thing: "send me the OTP",
# "what is his pin number". Giving one's own details ("my account number is 4411") or mentioning a code of conduct is not.
_ASK = (
    r"(?:send|give|share|tell|provide|need|want|what(?:'s| is| are)|read(?: out)?|forward|text|whatsapp|collect|get|"
    r"reveal|pass|ask(?:ed|ing)? (?:you )?for|looking for|hand over|let me (?:know|have))"
)
# The determiner after the verb ("send me THE otp", "what is HIS pin") is what makes it a request for someone's
# secret; "send quote, my account number is 4111" has none.
_DET = r"(?:(?:the|his|her|their|your|ava's|that|this|an?)\s+)"
_CREDENTIAL = _any(
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}?(?:otp|one[- ]time (?:code|pass(?:word|code)?)|verification code|security code|auth(?:entication)? code|2fa|mfa)\b",
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}(?:pass ?(?:word|code)|pin(?: number| code)?|cvv|cvc|api[- ]?key|secret key|private key|seed phrase|recovery phrase)\b",
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}(?:\w+ )?(?:door|gate|alarm|entry|access|lock|garage|wifi|wi-fi|locker) ?(?:code|pin|combination|password)\b",
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}(?:login|log in|sign[- ]?in) (?:details|credentials|info(?:rmation)?)\b",
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}(?:card|account|credit card|debit card|aadhaar|aadhar|social security|ssn|passport) (?:number|no\.?|details|digits)\b",
    r"\b(?:contrasena|clave|mot de passe)\b",
)
_PAYMENT = _any(
    r"\b(?:send|transfer|wire|lend|loan|remit|venmo|zelle|paypal|cashapp|cash app)\b.{0,25}"
    r"(?:\$|usd|eur|inr|rs\.?|rupees?|dollars?|euros?|pounds?|money|cash|funds|payment|upi|crypto|bitcoin|btc|usdt|eth)\b",
    r"\bpay (?:me|us)\b",
    r"\b(?:gift ?cards?|itunes card|google play card|steam card|western union|moneygram|bitcoin|usdt|crypto wallet)\b",
    rf"\b{_ASK}\b.{{0,25}}\b{_DET}(?:bank|wire|account|upi|payment|iban|swift|routing) (?:details|info(?:rmation)?|id)\b",
    r"\bprocessing fee\b|\bclaim your (?:prize|refund|reward)\b",
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
