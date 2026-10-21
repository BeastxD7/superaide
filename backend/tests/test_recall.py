"""Recall: spellings, synonyms, typos, any-word fallback and channels, all behind the gate."""

from __future__ import annotations

import pytest

from superaide.context import lexicon
from superaide.context.audience import Audience
from superaide.context.recall import pre_recall, recall
from superaide.db.models import Context, Event, MemoryItem
from superaide.db.session import session_scope


@pytest.fixture
def people(manager, colleague):
    return manager.id, colleague.id


def _note(s, owner, content, allowed=None, origin=None) -> MemoryItem:
    m = MemoryItem(
        content=content, subject_person_ids=[owner], allowed_kind="persons",
        allowed_person_ids=allowed or [owner], min_trust=2, category="preference", origin_context_id=origin,
    )  # fmt: skip
    s.add(m)
    s.flush()
    return m


def _ctx(s, provider: str, ext: str, kind: str = "dm") -> Context:
    c = Context(provider=provider, tenant_scope="", external_id=ext, kind=kind, audience_class="private_1to1")
    s.add(c)
    s.flush()
    return c


def _msg(s, ctx, text, audience, unknown=False) -> Event:
    e = Event(context_id=ctx.id, direction="in", content=text, audience_person_ids=audience,
              audience_has_unknown=unknown)  # fmt: skip
    s.add(e)
    s.flush()
    return e


def owner_only(pid) -> Audience:
    return Audience(person_ids={pid}, min_trust=2)


def test_spellings_and_synonyms_widen_each_word():
    assert lexicon.spellings("colours") == {"colours", "colors"}
    assert lexicon.spellings("organizing") == {"organizing", "organising"}
    assert "hate" in lexicon.variants("dislike") and "like" in lexicon.variants("dislike")
    # Short roots only swap as whole words.
    assert lexicon.spellings("moment") == {"moment"} and lexicon.spellings("mum") == {"mum", "mom"}
    assert lexicon.words("What do I really dislike?") == ["dislike"]
    assert lexicon.normalise("Shashank dislikes the COLOR black.") == "shashank dislikes the colour black"


@pytest.mark.parametrize(
    "query",
    ["black colour favourite color preference", "black color", "what colours do I dislike", "colors I hate",
     "do i like black colour?"],
)  # fmt: skip
def test_paraphrases_find_the_note(people, query):
    owner, _ = people
    with session_scope() as s:
        _note(s, owner, "Shashank does not like the colour black.")
        _note(s, owner, "Shashank's flight to Tokyo is on Friday.")
        found = recall(s, owner_only(owner), query)
        assert found and found[0].text == "Shashank does not like the colour black."


def test_a_typo_in_the_message_is_found_by_trigram(people):
    owner, _ = people
    with session_scope() as s:
        tg = _ctx(s, "telegram", "1001")
        _msg(s, tg, "btw i dont like black clour at all!", [owner])
        assert any("clour" in src.text for src in recall(s, owner_only(owner), "colour"))


def test_a_channel_named_in_the_query_filters_instead_of_searching(people):
    owner, _ = people
    with session_scope() as s:
        tg, web = _ctx(s, "telegram", "1001"), _ctx(s, "web", "web:x")
        _msg(s, tg, "I'm off to Goa next week", [owner])
        _msg(s, web, "Goa plans are on hold", [owner])
        found = recall(s, owner_only(owner), "Telegram")
        assert [f.text for f in found] == ["I'm off to Goa next week"]
        assert found[0].uri.startswith("ava://msg/telegram/")
        only_web = recall(s, owner_only(owner), "Goa", channel="web")
        assert [f.text for f in only_web] == ["Goa plans are on hold"]


def test_a_note_says_which_channel_it_was_learned_on(people):
    owner, _ = people
    with session_scope() as s:
        tg = _ctx(s, "telegram", "1001")
        _note(s, owner, "Shashank does not like the colour black.", origin=tg.id)
        assert recall(s, owner_only(owner), "black")[0].title == "Note · preference · from Telegram"


@pytest.mark.parametrize("query", ["Bangalore lease", "Bangalor", "Bangalore OR nothing", "Bangalore"])
def test_no_search_returns_what_the_audience_may_not_see(people, query):
    owner, colleague = people
    with session_scope() as s:
        slack = _ctx(s, "slack", "D_PRIYA")
        _note(s, owner, "Bangalore office lease ends in March", allowed=[colleague])
        _msg(s, slack, "Bangalore lease budget", [colleague])
        assert recall(s, owner_only(owner), query) == []
        assert recall(s, owner_only(owner), query, channel="slack") == []
        # In a group (unknown members) nothing private comes back, not even the owner's own.
        _note(s, owner, "Bangalore is where Shashank grew up")
        group = Audience(person_ids={owner}, has_unknown=True)
        assert recall(s, group, query) == []


def test_pre_recall_needs_every_word_so_it_stays_quiet_on_small_talk(people):
    owner, _ = people
    with session_scope() as s:
        _note(s, owner, "Shashank does not like the colour black.")
        assert [f.text for f in pre_recall(s, owner_only(owner), "what colors do I hate?")] == [
            "Shashank does not like the colour black."
        ]
        assert pre_recall(s, owner_only(owner), "black coffee please") == []
        assert pre_recall(s, owner_only(owner), "hi!") == []


@pytest.mark.parametrize(
    ("note", "query"),
    [("Shashank n'aime pas le café noir", "café noir"), ("शशांक को काला रंग पसंद नहीं है", "काला रंग"),
     ("黒は嫌い", "黒は嫌い"), ("Shashank's dog is called Bruno", "what's my dog's name?")],
)  # fmt: skip
def test_other_languages_and_apostrophes_still_match_word_for_word(people, note, query):
    owner, _ = people
    with session_scope() as s:
        _note(s, owner, note)
        assert [f.text for f in recall(s, owner_only(owner), query)] == [note]


def test_tokens_never_carry_tsquery_syntax():
    assert lexicon.words("a & b | !c (d) e:* 'f' <-> g") == ["b", "c", "d", "e", "f", "g"]


def test_a_question_with_two_typos_still_finds_the_note_before_the_model_runs(people):
    # Live eval 2026-10-22: "whats my favrite cusine again" got "I don't have a source".
    owner, colleague = people
    with session_scope() as s:
        _note(s, owner, "Shashank's favourite cuisine is Thai food.")
        _note(s, owner, "Shashank does not like the colour black.")
        found = pre_recall(s, owner_only(owner), "whats my favrite cusine again")
        assert [f.text for f in found] == ["Shashank's favourite cuisine is Thai food."]


def test_one_close_word_among_several_is_not_enough_to_attach_a_note(people):
    owner, colleague = people
    with session_scope() as s:
        _note(s, owner, "Shashank's favourite cuisine is Thai food.")
        # Only "favrite" is close; "colour" and "dislike" match nothing here.
        assert pre_recall(s, owner_only(owner), "which colour do I dislike, my favrite?") == []


def test_typo_matching_stays_behind_the_audience_gate(people):
    owner, colleague = people
    with session_scope() as s:
        _note(s, owner, "Shashank's favourite cuisine is Thai food.")  # owner only
        assert pre_recall(s, owner_only(colleague), "whats his favrite cusine") == []
        assert pre_recall(s, Audience(person_ids={owner}, has_unknown=True), "favrite cusine") == []
