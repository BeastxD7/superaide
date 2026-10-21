"""Words that mean the same thing to the owner but not to Postgres full-text search.

Postgres stems English ("colours" → "colour") but knows nothing of en-GB/en-US spelling
("colour" ≠ "color") or of preference words ("dislike", "hate", "doesn't like"). This module
turns a question into term groups: each group is one word of the question plus its spelling
variants and, for a few preference words, its synonyms. Recall then asks for every group
(AND) and, failing that, for any of them (OR). Kept in code rather than a Postgres thesaurus,
which needs files on the database server that neither the Docker image nor the embedded
Postgres lets us ship.

Other languages degrade gracefully: their words pass through unchanged (any script), so
recall still matches them exactly and by trigram; only the spelling and synonym widening is
English. Paraphrase in other languages is the embedder's job (ADR 0008 WP2b).
"""

from __future__ import annotations

import unicodedata

# en-GB root → en-US root. Matched as a prefix, so inflections follow ("colours", "organising").
GB_US_ROOTS: dict[str, str] = {
    # -our / -or
    "armour": "armor",
    "behaviour": "behavior",
    "colour": "color",
    "endeavour": "endeavor",
    "favour": "favor",
    "flavour": "flavor",
    "harbour": "harbor",
    "honour": "honor",
    "humour": "humor",
    "labour": "labor",
    "neighbour": "neighbor",
    "odour": "odor",
    "parlour": "parlor",
    "rumour": "rumor",
    "savour": "savor",
    "vapour": "vapor",
    # -ise / -ize, -yse / -yze
    "analys": "analyz",
    "apologis": "apologiz",
    "categoris": "categoriz",
    "criticis": "criticiz",
    "customis": "customiz",
    "emphasis": "emphasiz",
    "finalis": "finaliz",
    "memoris": "memoriz",
    "minimis": "minimiz",
    "optimis": "optimiz",
    "organis": "organiz",
    "paralys": "paralyz",
    "personalis": "personaliz",
    "prioritis": "prioritiz",
    "realis": "realiz",
    "recognis": "recogniz",
    "specialis": "specializ",
    "summaris": "summariz",
    "utilis": "utiliz",
    "visualis": "visualiz",
    # -re / -er
    "calibre": "caliber",
    "centre": "center",
    "fibre": "fiber",
    "kilometre": "kilometer",
    "litre": "liter",
    "metre": "meter",
    "theatre": "theater",
    # -ogue / -og
    "analogue": "analog",
    "catalogue": "catalog",
    "dialogue": "dialog",
    # doubled l
    "cancell": "cancel",
    "counsell": "counsel",
    "jewellery": "jewelry",
    "labell": "label",
    "modell": "model",
    "travell": "travel",
    # single words
    "aeroplane": "airplane",
    "ageing": "aging",
    "aluminium": "aluminum",
    "cosy": "cozy",
    "defence": "defense",
    "enrol": "enroll",
    "fulfil": "fulfill",
    "grey": "gray",
    "judgement": "judgment",
    "licence": "license",
    "mould": "mold",
    "moustache": "mustache",
    "mum": "mom",
    "offence": "offense",
    "programme": "program",
    "pyjama": "pajama",
    "sceptic": "skeptic",
    "tonne": "ton",
    "yoghurt": "yogurt",
}
US_GB_ROOTS = {us: gb for gb, us in GB_US_ROOTS.items()}

# A few preference words, so "what do I dislike" finds "doesn't like" and "hates".
# "like" sits in the dislike group because "does not like" stems to 'like'.
_SYNONYM_GROUPS: list[set[str]] = [
    {"dislike", "dislikes", "hate", "hates", "detest", "loathe", "like", "stand", "avoid"},
    {"like", "likes", "love", "loves", "enjoy", "enjoys", "prefer", "prefers", "fond", "favourite", "favorite"},
    {"favourite", "favorite", "prefer", "preference", "like", "love"},
    {"allergic", "allergy", "allergies", "intolerant"},
    {"vegetarian", "vegan", "veggie"},
]
SYNONYMS: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _w in _group:
        SYNONYMS.setdefault(_w, set()).update(_group - {_w})

# Words that never help a search (Postgres drops its own stop words; these are ours).
STOP = {
    "a", "about", "all", "don", "s", "t", "an", "and", "any", "are", "at", "ava", "btw", "can", "could", "did", "do",
    "does", "dont", "for", "from", "not", "really",
    "had", "has", "have", "how", "i", "is", "it", "know", "me", "my", "of", "on", "or", "remember",
    "said", "say", "tell", "that", "the", "this", "to", "told", "was", "what", "when", "where",
    "which", "who", "why", "with", "you", "your",
}  # fmt: skip


def _tokens(text: str) -> list[str]:
    """Runs of letters, marks and digits in any script ("colour", "café", "काला", "黒"). Nothing
    else gets through, so a token can never carry tsquery syntax (& | ! ( ) : * '). Apostrophes
    split words ("don't" → "don", "t"), as Postgres's parser does."""
    out: list[str] = []
    cur: list[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] in "LMN":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _plain(text: str) -> str:
    """Lowercased and NFC-normalised; accents are kept, because the stored text keeps them."""
    return unicodedata.normalize("NFC", text.lower())


def words(text: str) -> list[str]:
    """The searchable words of a question, in order, without our stop words."""
    out: list[str] = []
    for w in _tokens(_plain(text)):
        if w not in STOP and w not in out:
            out.append(w)
    return out


def _swap(word: str, table: dict[str, str]) -> str | None:
    """`word` with its root swapped by `table`, or None. Short roots ("mum", "ton") must be
    the whole word (or its plural), so "moment" and "tonight" are left alone."""
    for root, other in table.items():
        if len(root) < 5:
            if word in (root, root + "s"):
                return other + word[len(root) :]
        elif word.startswith(root):
            return other + word[len(root) :]
    return None


def spellings(word: str) -> set[str]:
    """The word in both en-GB and en-US spelling (itself included)."""
    out = {word}
    for table in (GB_US_ROOTS, US_GB_ROOTS):
        if (other := _swap(word, table)) is not None:
            out.add(other)
    return out


def variants(word: str) -> set[str]:
    """Spellings plus synonyms (and their spellings)."""
    out = spellings(word)
    for syn in SYNONYMS.get(word, set()):
        out |= spellings(syn)
    return out


def groups(text: str, limit: int = 12) -> list[set[str]]:
    """One set of interchangeable words per searchable word of the question."""
    return [variants(w) for w in words(text)[:limit]]


def normalise(text: str) -> str:
    """A canonical form for comparing two facts: plain words, en-GB spelling, no punctuation."""
    return " ".join(_swap(w, US_GB_ROOTS) or w for w in _tokens(_plain(text)))
