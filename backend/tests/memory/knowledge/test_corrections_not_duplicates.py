"""A correction that changes one date or number is not "said again" (#554)."""

from __future__ import annotations

import pytest

from superaide.identity.models import Person
from superaide.memory.knowledge.store import figures, find_same_fact, remember_fact
from superaide.platform.db.session import session_scope


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("The offsite is on 12 December in Goa.", "The offsite is on 19 December in Goa.", False),
        ("The offsite is on 12 December in Goa.", "The offsite is on 12 January in Goa.", False),
        (
            "Jordan's budget limit for the Pune hire is 90 lakh.",
            "Jordan's budget limit for the Pune hire is 80 lakh.",
            False,
        ),
        ("The budget is 1,50,000 rupees.", "The budget is 150000 rupees.", True),
        ("The offsite is on 12 December in Goa.", "The offsite is on 12 December in Goa!", True),
        ("Jordan prefers window seats.", "Jordan prefers window seats on flights.", True),
    ],
)
def test_figures_tell_a_correction_from_a_repeat(a, b, same):
    assert (figures(a) == figures(b)) is same


def test_a_near_identical_fact_with_another_date_is_not_found_as_the_same_fact(agent):
    with session_scope() as s:
        owner = Person(display_name="Jordan Whitfield", relation="unknown")
        s.add(owner)
        s.flush()
        remember_fact(s, owner=owner, content="The offsite is on 12 December in Goa.", category="work")
        assert find_same_fact(s, owner, "The offsite is on 19 December in Goa.") is None
        assert find_same_fact(s, owner, "The offsite is on 12 December in Goa") is not None
        # a repeat that only differs in wording still merges when every figure agrees
        assert find_same_fact(s, owner, "the offsite is on 12 december in goa, as planned", similarity=0.5) is not None
