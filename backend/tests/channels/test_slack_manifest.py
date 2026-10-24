"""The Slack app manifest travels with the Slack adapter (ADR 0015 phase 5): the connect guide embeds it."""

from __future__ import annotations

from superaide.app.connections import slack_manifest_url


def test_the_connect_guide_embeds_the_manifest_that_sits_next_to_the_adapter() -> None:
    url = slack_manifest_url()
    assert "manifest_yaml=" in url, "the manifest file was not found: the guide fell back to a blank Slack app"
