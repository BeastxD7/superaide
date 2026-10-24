"""Ava School playbook (#57), the prompt side: a lesson that passed its eval and is switched on reaches the owner's
own chat prompt, and nothing else: not with either flag off, not when it is unswitched or edited, and never in a
colleague's prompt."""

from __future__ import annotations

import pytest

from superaide.db.session import session_scope
from superaide.school import playbook
from superaide.school.models import Lesson
from tests.conftest import COLLEAGUE_SLACK, MANAGER_TG, sl, tg

GUIDANCE = "Always open a status update with the decision I must make, in one line."


@pytest.fixture
def lesson_on(manager):
    spec = {"say": "status?", "expect": [{"reply_contains": ["decision"]}]}
    with session_scope() as s:
        s.add(
            Lesson(
                owner_person_id=manager.id,
                role="personal assistant",
                text="status first",
                source="own",
                status="active",
                playbook_text=GUIDANCE,
                eval_spec=spec,
                eval_status="passed",
                eval_hash=playbook.spec_hash(GUIDANCE, spec),
                in_prompt=True,
            )  # fmt: skip
        )


def flags(settings, school=True, play=True):
    settings.school_enabled, settings.school_playbook_enabled = school, play


def test_owner_prompt_carries_the_lesson_when_on(agent, manager, model, settings, lesson_on):
    flags(settings)
    agent.handle(tg(MANAGER_TG, "status on the vendor?"))
    assert GUIDANCE in model.systems[-1]


@pytest.mark.parametrize("school,play", [(False, True), (True, False), (False, False)])
def test_flags_off_means_never(agent, manager, model, settings, lesson_on, school, play):
    flags(settings, school, play)
    agent.handle(tg(MANAGER_TG, "status on the vendor?"))
    assert GUIDANCE not in model.systems[-1]


def test_a_colleague_never_sees_it(agent, manager, colleague, model, settings, lesson_on):
    flags(settings)
    agent.handle(sl(COLLEAGUE_SLACK, "status on the vendor?"))
    assert all(GUIDANCE not in s for s in model.systems)


def test_an_unswitched_lesson_is_not_read(agent, manager, model, settings, lesson_on):
    flags(settings)
    with session_scope() as s:
        s.query(Lesson).update({"in_prompt": False})
    agent.handle(tg(MANAGER_TG, "status on the vendor?"))
    assert GUIDANCE not in model.systems[-1]
