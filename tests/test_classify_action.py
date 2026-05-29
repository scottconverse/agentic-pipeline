# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the classify_action risk classifier.

Table-driven coverage over the four risk classes plus the default, plus
the requires_judge helper and the pass-an-explicit-config path. These pin
that the judge layer routes the right actions: external_facing and
high_risk stop for the judge; reversible_write and read_only do not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import classify_action  # noqa: E402
from classify_action import classify_action as classify  # noqa: E402
from classify_action import load_classification, requires_judge  # noqa: E402


# (tool_name, command, expected_class)
CLASSIFY_CASES = [
    # read_only — observation, no state change
    ("bash", "cat README.md", "read_only"),
    ("bash", "git status", "read_only"),
    ("bash", "git log --oneline", "read_only"),
    ("bash", "git diff HEAD~1", "read_only"),
    ("bash", "grep -rn TODO src", "read_only"),
    ("bash", "pytest tests/ -q", "read_only"),
    ("bash", "ls -la", "read_only"),

    # reversible_write — local writes recoverable via git / undo
    ("bash", "git add .", "reversible_write"),
    ("bash", 'git commit -m "wip"', "reversible_write"),
    ("bash", "pip install requests", "reversible_write"),
    ("bash", "mkdir build", "reversible_write"),
    ("bash", "mv a.txt b.txt", "reversible_write"),
    ("Write", "", "reversible_write"),
    ("Edit", "", "reversible_write"),
    ("str_replace_editor", "", "reversible_write"),
    ("create_file", "", "reversible_write"),
    ("MultiEdit", "", "reversible_write"),

    # external_facing — leaves the local machine
    ("bash", "git push origin feature-x", "external_facing"),
    ("bash", "gh pr create --fill", "external_facing"),
    ("bash", "gh issue create --title bug", "external_facing"),
    ("bash", "curl -X POST https://api.example.com/hook", "external_facing"),
    ("bash", "curl --data @body.json https://api.example.com", "external_facing"),
    ("bash", "docker push myorg/img:latest", "external_facing"),
    ("bash", "kubectl apply -f deploy.yaml", "external_facing"),

    # high_risk — irreversible / shared state / credential-touching
    ("bash", "rm -rf build", "high_risk"),
    ("bash", "git push origin main", "high_risk"),
    ("bash", "git push --force origin feature-x", "high_risk"),
    ("bash", "sudo systemctl restart nginx", "high_risk"),
    ("bash", 'psql -c "DROP TABLE users"', "high_risk"),
    ("bash", "export AWS_SECRET_KEY=abc123", "high_risk"),
    ("bash", "npm publish", "high_risk"),
    # human_only_under_autonomous is reported as high_risk
    ("bash", "gh release create v1.0.0", "high_risk"),
    ("bash", "gh pr merge 12 --admin", "high_risk"),
    ("bash", "git push --tags origin", "high_risk"),

    # default — unmatched command falls back to reversible_write
    ("bash", "frobnicate --widget 7", "reversible_write"),
    ("bash", "some-unknown-tool", "reversible_write"),
]


@pytest.mark.parametrize("tool_name,command,expected", CLASSIFY_CASES)
def test_classify_action(tool_name, command, expected):
    assert classify(tool_name, command) == expected


def test_most_restrictive_wins_across_classes():
    """A command matching both read_only and high_risk classifies as the
    most restrictive (high_risk), so the judge sees the worst case."""
    assert classify("bash", "cat secrets.txt && rm -rf /tmp/x") == "high_risk"


def test_redirect_write_beats_read_only():
    """`git diff > out.txt` is a write (redirect), not read_only."""
    assert classify("bash", "git diff > out.txt") == "reversible_write"


@pytest.mark.parametrize(
    "risk_class,expected",
    [
        ("high_risk", True),
        ("external_facing", True),
        ("reversible_write", False),
        ("read_only", False),
        ("unknown_class", False),
    ],
)
def test_requires_judge(risk_class, expected):
    assert requires_judge(risk_class) is expected


def test_classify_with_explicit_config_dict():
    """Passing a config dict bypasses the file read entirely."""
    config = {
        "classification": {
            "high_risk": [{"pattern": r"\bboom\b", "tool": "bash"}],
            "read_only": [{"pattern": r"^peek\b", "tool": "bash"}],
        },
        "default_class": "reversible_write",
    }
    assert classify("bash", "boom now", config=config) == "high_risk"
    assert classify("bash", "peek here", config=config) == "read_only"
    assert classify("bash", "whatever", config=config) == "reversible_write"


def test_load_classification_reads_the_repo_file():
    """load_classification with no args resolves the canonical file and
    returns the four-class taxonomy plus the default."""
    data = load_classification()
    classes = data.get("classification", {})
    for expected_class in (
        "human_only_under_autonomous",
        "high_risk",
        "external_facing",
        "reversible_write",
        "read_only",
    ):
        assert expected_class in classes, f"missing class {expected_class}"
    assert data.get("default_class") == "reversible_write"


def test_default_classification_path_points_at_pipelines():
    assert classify_action.DEFAULT_CLASSIFICATION_PATH.name == "action-classification.yaml"
    assert classify_action.DEFAULT_CLASSIFICATION_PATH.parent.name == "pipelines"


def test_cli_prints_the_risk_class(capsys):
    """run.md Step 7a invokes `python scripts/classify_action.py <tool> "<cmd>"`
    as the helper — so the script must actually be runnable as a CLI and print
    exactly the class name. (The live e2e surfaced that the CLI was missing.)"""
    rc = classify_action.main(["bash", "gh release create v0.4"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "high_risk"


def test_cli_honors_an_explicit_config_path(tmp_path, capsys):
    cfg = tmp_path / "action-classification.yaml"
    cfg.write_text(
        "classification:\n"
        "  external_facing:\n"
        "    - pattern: 'curl'\n"
        "      tool: bash\n"
        "default_class: reversible_write\n",
        encoding="utf-8",
    )
    rc = classify_action.main(
        ["bash", "curl https://example.com", "--config-path", str(cfg)]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == "external_facing"
