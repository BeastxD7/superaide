"""CI rule: every PR body shows a Before and an After image (scripts/check_pr_evidence.py, issue #662)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
RENDER = ROOT / "scripts" / "render_evidence.py"
spec = importlib.util.spec_from_file_location("check_pr_evidence", ROOT / "scripts" / "check_pr_evidence.py")
assert spec and spec.loader
ev = importlib.util.module_from_spec(spec)
sys.modules["check_pr_evidence"] = ev  # dataclasses look the module up by name
spec.loader.exec_module(ev)

RAW = "https://raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-700"
BEFORE = f"![before]({RAW}/before-chat.png)"
AFTER = f"![after]({RAW}/after-chat.png)"

PASSING = {
    "sections": f"## Before and after\n\n### Before (main)\n\n{BEFORE}\n\n### After (this branch)\n\n{AFTER}\n",
    "separate headings": f"## Before\n{BEFORE}\n\n## After\n{AFTER}\n",
    "old template table": f"## Screenshots\n\n| Before | After |\n| --- | --- |\n| {BEFORE} | {AFTER} |\n",
    "table with row labels": (
        f"| | 1280 |\n| --- | --- |\n| Before (Slack) | ![]({RAW}/b.png) |\n| After (Slack) | ![]({RAW}/a.png) |\n"
    ),
    "label lines": (
        f"Before: Jordan Whitfield saw a 404.\n\n![x]({RAW}/one.png)\n\nAfter: the page loads.\n\n![x]({RAW}/two.png)\n"
    ),
    "alt text and file name": f"## Before and after\n\n![before 1280]({RAW}/x.png)\n![after 1280]({RAW}/y.png)\n",
    "html img": f'## Before\n<img src="{RAW}/b.png" width="400">\n## After\n<img alt="after" src="{RAW}/a.png">\n',
    "github attachments": (
        "### Before\n![a](https://github.com/user-attachments/assets/1111-aaaa)\n"
        "### After\n![b](https://github.com/user-attachments/assets/2222-bbbb)\n"
    ),
    "crlf line endings": f"## Before\r\n{BEFORE}\r\n## After\r\n{AFTER}\r\n",
}

FAILING = {
    "empty body": "",
    "no images, only text": "## Before and after\n\nBefore: it failed.\nAfter: it passes.\n```\n1 passed\n```\n",
    "only a before image": f"## Before\n{BEFORE}\n## After\nTests pass.\n",
    "only an after image": f"## Before\nIt was broken.\n## After\n{AFTER}\n",
    "one image for both": f"## Before and after\n\n![before and after]({RAW}/before-after.png)\n",
    "images under no label": f"## Notes\n\n![a]({RAW}/one.png)\n![b]({RAW}/two.png)\n",
    "template left empty": (
        "## Screenshots\n\n<!-- drag images in. ![before](https://github.com/user-attachments/assets/x) -->\n"
        "| Before | After |\n| --- | --- |\n| | |\n\n- [ ] N/A: no UI change\n"
    ),
    "image inside a code block": f"## Before\n```\n{BEFORE}\n```\n## After\n```\n{AFTER}\n```\n",
    "images hosted elsewhere": "## Before\n![b](https://example.com/b.png)\n## After\n![a](https://example.com/a.png)\n",
    "evidence branch of another repo": (
        "## Before\n![b](https://raw.githubusercontent.com/someone/else/evidence/b.png)\n"
        "## After\n![a](https://raw.githubusercontent.com/someone/else/evidence/a.png)\n"
    ),
    "a badge is not evidence": "## Before\n![b](https://img.shields.io/badge/x-y-green)\n## After\n![a](https://img.shields.io/a.svg)\n",
}


@pytest.mark.parametrize("name", PASSING)
def test_bodies_with_a_before_and_an_after_image_pass(name):
    res = ev.evaluate(PASSING[name])
    assert res.ok, name
    assert res.before and res.after
    assert ev.message(res, ev.DEFAULT_REPO) == []


@pytest.mark.parametrize("name", FAILING)
def test_bodies_without_both_images_fail(name):
    assert not ev.evaluate(FAILING[name]).ok, name


def test_the_failure_message_says_exactly_what_to_add():
    msg = "\n".join(ev.message(ev.evaluate(FAILING["only a before image"]), ev.DEFAULT_REPO))
    assert "no After image" in msg and "no Before" not in msg.split("Found:")[0].replace("no After", "")
    assert "### Before (main)" in msg and "### After (this branch)" in msg
    assert "raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-<n>/" in msg
    assert "re-runs when the body is edited" in msg
    assert "render_evidence.py" in msg
    both = "\n".join(ev.message(ev.evaluate(""), ev.DEFAULT_REPO))
    assert "no Before and no After image" in both


def test_unclassified_and_foreign_images_are_explained():
    res = ev.evaluate(FAILING["images under no label"])
    assert len(res.unclassified) == 2
    assert "under no Before or After" in "\n".join(ev.message(res, ev.DEFAULT_REPO))
    res = ev.evaluate(FAILING["images hosted elsewhere"])
    assert len(res.bad_host) == 2
    assert "https://example.com/b.png" in "\n".join(ev.message(res, ev.DEFAULT_REPO))


def test_dependabot_is_the_only_exemption():
    assert ev.evaluate("", author="dependabot[bot]").ok
    assert not ev.evaluate("", author="BeastxD7").ok
    assert not ev.evaluate("", author="dependabot-lookalike").ok
    # a label, a docs title or a bot-sounding body do not exempt anything
    assert not ev.evaluate("docs: typo. No UI change. Labels: tests-not-needed", author="someone").ok


def _run(body: str, author: str = "BeastxD7"):
    import os

    env = {"PATH": os.environ["PATH"], "PR_BODY": body, "PR_AUTHOR": author, "REPO": "BeastxD7/superaide"}
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_pr_evidence.py")], env=env, capture_output=True, text=True
    )


def test_cli_exit_codes_and_annotation():
    ok = _run(PASSING["sections"])
    assert ok.returncode == 0 and "ok:" in ok.stdout
    bad = _run("Just words.")
    assert bad.returncode == 1
    assert bad.stdout.startswith("::error title=Before and after images missing::")
    assert "### After (this branch)" in bad.stdout
    assert _run("", author="dependabot[bot]").returncode == 0


def test_the_job_is_wired_into_the_gate_and_reruns_on_edit():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    triggers = ci.get("on") or ci.get(True)
    assert "edited" in triggers["pull_request"]["types"]
    job = ci["jobs"]["evidence"]
    assert job["if"] == "github.event_name == 'pull_request'"
    env = job["steps"][1]["env"]
    assert env["PR_BODY"] == "${{ github.event.pull_request.body }}"  # from the event payload, as an env var
    assert "dependabot" not in str(job).lower()  # the exemption lives in the script, in one place
    assert "evidence" in ci["jobs"]["result"]["needs"]
    run = ci["jobs"]["result"]["steps"][1]["run"]
    assert ".evidence.result" in run and "must not be skipped" not in run


def test_pr_template_and_rules_name_the_section_the_checker_reads():
    template = (ROOT / ".github" / "pull_request_template.md").read_text()
    assert "## Before and after" in template and "### Before" in template and "### After" in template
    # the unfilled template must fail, so nobody passes by leaving it as is
    assert not ev.evaluate(template).ok
    agents = (ROOT / "AGENTS.md").read_text()
    assert "check_pr_evidence.py" in agents and "render_evidence.py" in agents


def test_render_script_writes_an_html_card(tmp_path):
    out = tmp_path / "card.html"
    done = subprocess.run(
        [sys.executable, str(RENDER), str(out), "--title", "BEFORE (main)", "--html-only"],
        input="\x1b[31mFAILED\x1b[0m tests/x.py <b>\n1 failed\n",
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    page = out.read_text()
    assert "BEFORE (main)" in page and "FAILED" in page and "&lt;b&gt;" in page and "\x1b" not in page
    empty = subprocess.run(
        [sys.executable, str(RENDER), str(out), "--html-only"],
        input="",
        capture_output=True,
        text=True,
    )
    assert empty.returncode != 0
