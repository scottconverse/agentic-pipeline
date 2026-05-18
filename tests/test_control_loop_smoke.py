# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the v2.0 control-loop scripts.

Pass 13 (audit Cluster M / TEST-001) closes a coverage gap: pre-Pass-13
the five control-loop scripts (`check_pipeline_control_loop.py`,
`stop_validator.py`, `final_response_gate.py`, `pipeline_continue.py`,
`agent_decision_gate.py`) had zero direct test coverage. They were
exercised transitively by the hooks tests but a regression in their
public surface wouldn't be caught at the unit layer.

This file provides one small happy-path + one negative case for each
script's public API. Comprehensive coverage is a future expansion;
this is the floor.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# check_pipeline_control_loop.parse_control_state + validate_control_state
# ---------------------------------------------------------------------------


def test_parse_control_state_extracts_yaml_fields() -> None:
    import check_pipeline_control_loop as cpcl

    text = (
        "active_run: true\n"
        "current_stage: plan\n"
        "next_required_action: write plan.md\n"
        "stop_condition: none\n"
        "final_response_allowed: false\n"
        "continuing_to: research\n"
    )
    fields = cpcl.parse_control_state(text)
    assert fields["active_run"] == "true"
    assert fields["current_stage"] == "plan"
    assert fields["next_required_action"] == "write plan.md"


_FULL_VALID_FIELDS = {
    "active_run": "true",
    "current_stage": "plan",
    "last_completed_gate": "manifest",
    "next_required_action": "write plan.md",
    "stop_condition": "none",
    "final_response_allowed": "false",
    "continuing_to": "research",
}


def test_validate_control_state_accepts_valid_state() -> None:
    import check_pipeline_control_loop as cpcl

    violations = cpcl.validate_control_state(dict(_FULL_VALID_FIELDS))
    assert violations == [], (
        f"valid state should have no violations; got {violations!r}"
    )


def test_validate_control_state_rejects_invalid_active_run() -> None:
    import check_pipeline_control_loop as cpcl

    fields = dict(_FULL_VALID_FIELDS)
    fields["active_run"] = "maybe"  # must be true/false
    violations = cpcl.validate_control_state(fields)
    assert any("`active_run` must be `true` or `false`" in v for v in violations)


def test_validate_control_state_rejects_active_run_without_stop_when_allowed() -> None:
    """active_run=true + final_response_allowed=true + stop_condition=none
    is the failure mode the gate exists to catch: pretending to be done
    while the run still has work pending."""
    import check_pipeline_control_loop as cpcl

    fields = dict(_FULL_VALID_FIELDS)
    fields["final_response_allowed"] = "true"
    violations = cpcl.validate_control_state(fields)
    assert any("cannot allow a final response without a valid stop condition" in v for v in violations)


# ---------------------------------------------------------------------------
# stop_validator: discover + active_state_files
# ---------------------------------------------------------------------------


def test_stop_validator_discovers_state_files(tmp_path: Path) -> None:
    import stop_validator as sv

    run = tmp_path / "test-run"
    run.mkdir()
    (run / "active-control-state.md").write_text(
        "active_run: true\nfinal_response_allowed: true\nstop_condition: human_approval\n",
        encoding="utf-8",
    )

    found = sv.discover_state_files(tmp_path)
    assert len(found) == 1
    assert found[0].name == "active-control-state.md"


def test_stop_validator_active_state_files_filters_inactive(tmp_path: Path) -> None:
    import stop_validator as sv

    run1 = tmp_path / "active-run"
    run1.mkdir()
    (run1 / "active-control-state.md").write_text(
        "active_run: true\nfinal_response_allowed: true\nstop_condition: human_approval\n",
        encoding="utf-8",
    )
    run2 = tmp_path / "inactive-run"
    run2.mkdir()
    (run2 / "active-control-state.md").write_text(
        "active_run: false\nfinal_response_allowed: true\n",
        encoding="utf-8",
    )

    active = sv.active_state_files(tmp_path)
    assert len(active) == 1
    assert "active-run" in str(active[0])


# ---------------------------------------------------------------------------
# final_response_gate: evaluate against an empty run dir
# ---------------------------------------------------------------------------


def test_final_response_gate_with_no_runs_returns_empty(tmp_path: Path) -> None:
    """No .agent-runs/ runs → no gate results to block on. The hook
    layer calls this with require_active_run=False; we mirror that."""
    import final_response_gate as frg

    results = frg.evaluate_final_response_gate(tmp_path, require_active_run=False)
    assert results == [] or all(r.allowed for r in results)


def test_final_response_gate_blocks_on_active_run_without_stop_condition(tmp_path: Path) -> None:
    """An active run with stop_condition=none and final_response_allowed=false
    blocks the final response."""
    import final_response_gate as frg

    run = tmp_path / "blocking-run"
    run.mkdir()
    (run / "active-control-state.md").write_text(
        "active_run: true\n"
        "current_stage: plan\n"
        "next_required_action: write plan.md\n"
        "stop_condition: none\n"
        "final_response_allowed: false\n"
        "continuing_to: research\n",
        encoding="utf-8",
    )

    results = frg.evaluate_final_response_gate(tmp_path, require_active_run=False)
    blocked = [r for r in results if not r.allowed]
    assert blocked, "active run with final_response_allowed=false must block"


# ---------------------------------------------------------------------------
# pipeline_continue: next_action import-only smoke
# ---------------------------------------------------------------------------


def test_pipeline_continue_next_action_imports_and_runs(tmp_path: Path) -> None:
    """pipeline_continue.next_action(run_dir) is the public API. Smoke
    test: import + call against an empty dir. Should not raise."""
    import pipeline_continue as pc

    rc, msg = pc.next_action(tmp_path)
    assert isinstance(rc, int)
    assert isinstance(msg, str)


# ---------------------------------------------------------------------------
# agent_decision_gate: module-level import smoke
# ---------------------------------------------------------------------------


def test_agent_decision_gate_imports_and_has_main() -> None:
    """Smoke check that agent_decision_gate imports without side effects
    and exposes a main() entrypoint. Detailed exit-code coverage is in
    the existing hooks tests that exercise the StopValidation surface
    indirectly."""
    import agent_decision_gate as adg

    assert callable(adg.main)
