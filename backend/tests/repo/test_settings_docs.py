"""docs/settings.md is generated from the settings definitions (#433): it must never be out of date."""

from __future__ import annotations

from pathlib import Path

from superaide.skills import settings_docs

PAGE = Path(__file__).resolve().parents[3] / "docs" / "settings.md"


def test_the_settings_page_matches_the_definitions():
    assert PAGE.read_text() == settings_docs.render(), (
        "docs/settings.md is out of date: run `uv run python -m superaide.skills.settings_docs > ../docs/settings.md` "
        "from backend/"
    )


def test_every_setting_is_listed_with_its_default():
    from superaide.channels import settings as channels
    from superaide.integrations.calendar import settings as calendar
    from superaide.integrations.gmail import settings as gmail
    from superaide.skills import retention_settings, web_settings

    page = PAGE.read_text()
    labels = [v[2] for v in gmail.CHOICES.values()]
    for mod in (calendar, web_settings, channels, retention_settings):
        labels += [c.label for c in mod.CHOICES.values()]
    assert all(label in page for label in labels)
    assert len(labels) >= 20
    assert "Fixed on purpose" in page and "Nothing is sent without your yes" in page
