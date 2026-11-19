"""#404: a flag that was promoted to always-on is ignored when still set, and startup says so once."""

import logging

from superaide.platform import retired
from superaide.platform.config import Settings


def test_a_retired_name_in_the_environment_is_reported_and_ignored(caplog, tmp_path):
    env = {"TYPING_INDICATORS": "false", "NOTIFY_FAILOVER": "false", "MAX_SPEND_USD": "5"}
    with caplog.at_level(logging.WARNING, logger=retired.log.name):
        found = retired.warn(env, env_file=tmp_path / "missing.env")
    assert found == ["NOTIFY_FAILOVER", "TYPING_INDICATORS"]
    assert sum("no longer a setting" in r.message for r in caplog.records) == 2
    assert all("MAX_SPEND_USD" not in r.message for r in caplog.records)


def test_a_retired_name_in_the_env_file_is_reported(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# TYPING_INDICATORS=true\nexport PIPELINE_TIMING_ENABLED=false\nMAX_SPEND_USD=5\n")
    assert retired.set_but_retired({}, env_file=f) == ["PIPELINE_TIMING_ENABLED"]  # a commented line does not count


def test_nothing_is_reported_when_none_is_set(caplog, tmp_path):
    with caplog.at_level(logging.WARNING, logger=retired.log.name):
        assert retired.warn({"MAX_SPEND_USD": "5"}, env_file=tmp_path / "none") == []
    assert caplog.records == []


def test_setting_a_retired_name_changes_nothing(monkeypatch):
    for name in retired.RETIRED:
        monkeypatch.setenv(name, "false")
    s = Settings(_env_file=None)
    assert not any(hasattr(s, n.lower()) for n in retired.RETIRED)


def test_the_retired_names_match_the_registry_and_the_generated_doc():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    reg = tomllib.loads((root / "backend" / "flags.toml").read_text())
    assert {r["name"]: r["now"] for r in reg["retired"]} == retired.RETIRED
    doc = (root / "docs" / "flags.md").read_text().split("## Retired settings")[1]
    for name in retired.RETIRED:
        assert f"| `{name}` |" in doc


def test_a_retired_name_is_not_a_flag_any_more():
    import tomllib
    from pathlib import Path

    reg = tomllib.loads((Path(__file__).resolve().parents[3] / "backend" / "flags.toml").read_text())
    assert not {f["name"] for f in reg["flag"]} & set(retired.RETIRED)
