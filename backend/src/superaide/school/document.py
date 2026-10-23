"""The lesson file: what leaves the machine when an owner exports, and what comes in on import (#57).

Only the lesson text and its role travel. No owner id, no instance id, no timestamps, no names. The
checksum is for catching a file edited by accident after review; it is not a signature, and an imported
lesson is untrusted text either way: it is stored pending, shown to the owner, and read by nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "superaide-lessons/1"
MAX_LESSONS = 50


class FileLesson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=2000)  # the length rule that matters is checked in code with a message
    role: str = Field(default="personal assistant", max_length=60)


class LessonFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str
    lessons: list[FileLesson] = Field(max_length=MAX_LESSONS)
    sha256: str = Field(max_length=64)


def checksum(lessons: list[dict[str, str]]) -> str:
    canon = json.dumps(lessons, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def build(lessons: list[dict[str, str]]) -> dict[str, Any]:
    return {"format": FORMAT, "lessons": lessons, "sha256": checksum(lessons)}


def parse(raw: Any) -> LessonFile:
    """Validate a file's shape; raises ValueError with a message the owner can act on."""
    try:
        doc = LessonFile.model_validate(raw)
    except Exception as exc:
        raise ValueError("That isn't a Superaide lesson file.") from exc
    if doc.format != FORMAT:
        raise ValueError(f"This file is {doc.format!r}; this version reads {FORMAT!r}.")
    if doc.sha256 != checksum([ls.model_dump() for ls in doc.lessons]):
        raise ValueError("The file was changed after it was exported, so it isn't read.")
    return doc
