"""Binding an approval to the exact content the owner was shown (issue #111).

An approval is a yes to *this* message, not to whatever is later found under the same draft id. The content
is reduced to a small canonical form and hashed when the approval is requested; the executor rebuilds it
from what is about to be sent and compares. Case and runs of white space are ignored, because Gmail
round-trips mail through MIME; everything else (recipients, thread, subject, words) must match.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def content_hash(*, to: Iterable[str], thread_id: str, subject: str, text: str) -> str:
    canonical = {
        "to": sorted({a.strip().lower() for a in to if a.strip()}),
        "thread": thread_id.strip(),
        "subject": " ".join(subject.split()).lower(),
        "text": " ".join(text.split()),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
