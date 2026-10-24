# ci-module: repo
"""Every label the workflows, scripts and Dependabot use is defined in .github/labels.yml.

The Labels workflow deletes labels that are not listed there, so a label used by CI but missing from the file
would silently disappear on the next sync (this happened to `tests-not-needed` and `full-suite`).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _defined() -> set[str]:
    text = (ROOT / ".github" / "labels.yml").read_text()
    return set(re.findall(r'^- name: "([^"]+)"', text, flags=re.M))


def test_labels_used_by_ci_are_defined():
    defined = _defined()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    used = {"tests-not-needed", "full-suite"}
    for label in used:
        assert label in ci or label in (ROOT / "scripts" / "tests_required.py").read_text(), label
    missing = used - defined
    assert not missing, f"add to .github/labels.yml or the sync deletes them: {sorted(missing)}"


def test_dependabot_labels_are_defined():
    defined = _defined()
    text = (ROOT / ".github" / "dependabot.yml").read_text()
    used: set[str] = set()
    for m in re.finditer(r"^\s*labels:\s*\[(.*)\]", text, flags=re.M):
        used |= {x.strip().strip('"') for x in m.group(1).split(",") if x.strip()}
    assert used and not (used - defined), sorted(used - defined)


def test_label_names_are_unique_with_colour_and_description():
    text = (ROOT / ".github" / "labels.yml").read_text()
    blocks = re.split(r"^- name:", text, flags=re.M)[1:]
    names = [b.split("\n", 1)[0].strip().strip('"') for b in blocks]
    assert len(names) == len(set(names))
    for b in blocks:
        assert re.search(r'^\s+color: "[0-9a-f]{6}"', b, flags=re.M), b
        assert re.search(r"^\s+description: ", b, flags=re.M), b
