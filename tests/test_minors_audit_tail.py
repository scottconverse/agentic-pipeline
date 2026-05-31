# SPDX-License-Identifier: Apache-2.0
"""Audit-tail Minors: ENG-011, ENG-012, QA-006, QA-007."""
from __future__ import annotations

import sys

import pytest


# --- ENG-011: check_no_todos scans scripts/ but self-excludes the detector ---


def test_scripts_dir_is_scanned():
    from scripts.check_no_todos import DEFAULT_EXCLUDED_DIRS

    assert "scripts" not in DEFAULT_EXCLUDED_DIRS, (
        "scripts/ must be scanned for TODO markers (ENG-011)"
    )


def test_check_no_todos_passes_on_live_repo():
    """Proves both halves of ENG-011 at once: scripts/ is now scanned AND the
    detector self-excludes its own marker literals. If the self-exclusion were
    missing, check_no_todos.py's own `TODO/FIXME/HACK` strings would flip this
    to a failure; if scripts/ weren't scanned, a real TODO there would slip."""
    from scripts.check_no_todos import main

    assert main() == 0


# --- ENG-012: context-window detection (single source of truth) -------------


@pytest.mark.parametrize(
    "model, expected",
    [
        ("claude-opus-4-8", 1_000_000),
        ("claude-sonnet-4-6", 1_000_000),
        ("CLAUDE-OPUS-4-8", 1_000_000),  # case-insensitive
        ("anything[1m]", 1_000_000),  # explicit suffix forces 1M
        ("claude-opus-4-9-future", 200_000),  # unrecognized -> conservative
        ("gpt-5", 200_000),
        (None, 200_000),
        ("", 200_000),
    ],
)
def test_detect_context_window(model, expected):
    from hooks.hook_utils import detect_context_window

    assert detect_context_window(model) == expected


def test_run_md_does_not_duplicate_the_model_list():
    """run.md must point at hook_utils rather than keep a second copy of the
    1M-model list (the ENG-012 lockstep trap). The authoritative set lives in
    `_CONTEXT_WINDOW_1M_PREFIXES`; run.md should reference it, not re-list."""
    from pathlib import Path

    run_md = (
        Path(__file__).resolve().parents[1]
        / "skills" / "run" / "references" / "run.md"
    ).read_text(encoding="utf-8")
    assert "_CONTEXT_WINDOW_1M_PREFIXES" in run_md
    assert "keep this list in lockstep" not in run_md


# --- QA-006: UTF-8 stdout helper --------------------------------------------


def test_ensure_utf8_stdout_is_safe_and_idempotent():
    from scripts.policy_utils import ensure_utf8_stdout

    # Must not raise, and is safe to call repeatedly.
    ensure_utf8_stdout()
    ensure_utf8_stdout()


def test_ensure_utf8_stdout_noop_on_unreconfigurable_stream(monkeypatch):
    from scripts import policy_utils

    class _NoReconfigure:
        pass  # deliberately lacks .reconfigure

    monkeypatch.setattr(policy_utils.sys, "stdout", _NoReconfigure())
    monkeypatch.setattr(policy_utils.sys, "stderr", _NoReconfigure())
    policy_utils.ensure_utf8_stdout()  # must be a silent no-op


# --- QA-007: classify_action robustness -------------------------------------


def test_empty_command_fails_closed_to_high_risk():
    from scripts.classify_action import classify_action

    assert classify_action("bash", "") == "high_risk"
    assert classify_action("bash", "   ") == "high_risk"
    assert classify_action("bash", None) == "high_risk"


def test_nonempty_unmatched_command_returns_default():
    from scripts.classify_action import classify_action

    cfg = {"classification": {}, "default_class": "read_only"}
    assert classify_action("bash", "ls -la", config=cfg) == "read_only"


def test_cli_missing_config_exits_2_without_traceback(capsys):
    from scripts.classify_action import main

    rc = main(["bash", "rm -rf /", "--config-path", "/no/such/path.yaml"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "config not found" in err
