# SPDX-License-Identifier: Apache-2.0
"""v2.2.1 modal-budget hook tests.

v2.1.0 introduced `modal_budget_decision` to deny `AskUserQuestion`
modals fired OUTSIDE declared `gate: human_approval` stages (allowing
modals AT the three declared gates: manifest / plan / manager).

v2.2.1 reverses the gate-stage exception: gates are now chat-based with
deterministic first-token keyword parsing. There are NO legitimate
`AskUserQuestion` calls during an active non-drafting pipeline run. The
modal-budget hook now denies EVERY modal during such a run, regardless
of stage.

Permits the modal only when:
  - no active non-drafting run exists (operator ad-hoc use is fine)
  - all active runs are in drafting state (intake mid-flight)

Closes the operator UX failure where Cowork's modal overlay hid the
chat context the operator needed at gate-decision time.
"""
from __future__ import annotations

import json
from pathlib import Path

from hooks import hook_runner


def _json_out(capsys):
    out = capsys.readouterr().out.strip()
    assert out, "expected a JSON hook payload, got empty output"
    return json.loads(out)


def _write_active_run_at_stage(root: Path, stage: str, last_completed: str = "") -> Path:
    """Active non-drafting run at a specific current_stage."""
    run = root / ".agent-runs" / "modal-budget-run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: " + stage,
                "last_completed_gate: " + last_completed,
                "next_required_action: doing the work",
                "stop_condition: none",
                "final_response_allowed: false",
                "continuing_to: next stage",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        "pipeline_run:\n  id: modal-budget-run\n  type: feature\n  allowed_paths:\n    - src\n",
        encoding="utf-8",
    )
    return run


# ---------------------------------------------------------------------------
# v2.2.1: deny-all-during-active-non-drafting-run semantics
# ---------------------------------------------------------------------------


def test_modal_budget_denies_at_former_manifest_gate_stage(tmp_path, capsys, monkeypatch):
    """v2.2.1: modal at manifest stage (was permitted under v2.1.0 as a
    declared gate) is NOW denied. Gates are chat-based; modals always
    denied during active non-drafting runs."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "manifest")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "approve manifest?", "header": "Manifest", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "MODAL_BUDGET_EXCEEDED" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_modal_budget_denies_at_former_plan_gate_stage(tmp_path, capsys, monkeypatch):
    """v2.2.1: modal at plan stage (was permitted under v2.1.0) is NOW denied."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "plan")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "approve plan?", "header": "Plan", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "MODAL_BUDGET_EXCEEDED" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_modal_budget_denies_at_former_manager_gate_stage(tmp_path, capsys, monkeypatch):
    """v2.2.1: modal at manager stage (was permitted under v2.1.0) is NOW denied."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "manager")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "approve manager verdict?", "header": "Manager", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "MODAL_BUDGET_EXCEEDED" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_modal_budget_denies_at_non_gate_stage(tmp_path, capsys, monkeypatch):
    """Modal at research stage (never a gate) is denied — same as v2.1.0."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "research")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "extra prompt?", "header": "Extra", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "MODAL_BUDGET_EXCEEDED" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_modal_budget_denies_even_without_pipeline_yaml(tmp_path, capsys, monkeypatch):
    """v2.2.1: no longer reads the pipeline yaml at all. An active
    non-drafting run with no .pipelines/feature.yaml still denies — the
    gate-stage exception is gone, so yaml resolution is unnecessary."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "plan")
    # No .pipelines/feature.yaml created on purpose

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "no yaml?", "header": "Q", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_modal_budget_denies_unknown_stage(tmp_path, capsys, monkeypatch):
    """v2.2.1: unknown current_stage + last_completed_gate combos no
    longer have any allow path. Active non-drafting run = deny, period."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "(unknown)", last_completed="manager")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "?", "header": "Q", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# Allow paths (unchanged from v2.1.0)
# ---------------------------------------------------------------------------


def test_modal_budget_bypasses_when_no_active_run(tmp_path, capsys, monkeypatch):
    """Outside an active pipeline run, AskUserQuestion is fine."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "ad-hoc?", "header": "Ad-hoc", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    assert capsys.readouterr().out == ""


def test_modal_budget_bypasses_during_drafting_run(tmp_path, capsys, monkeypatch):
    """Drafting (intake mid-flight) runs bypass modal budget — operator
    can use modals for intake clarifications before the pipeline starts."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = tmp_path / ".agent-runs" / "drafting-run"
    run.mkdir(parents=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: drafting",
                "current_stage: intake_drafted",
                "next_required_action: pending operator promotion",
                "stop_condition: awaiting_operator_completion",
                "final_response_allowed: true",
                "continuing_to: pipeline_start",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        "pipeline_run:\n  id: drafting-run\n  type: feature\n", encoding="utf-8"
    )

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "drafting?", "header": "Q", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny", "drafting run modals must not be denied; got " + str(payload)


def test_modal_budget_bypasses_non_ask_tools(tmp_path, capsys, monkeypatch):
    """Read/Bash/etc. during research stage don't trip modal budget."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "research")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "some.txt")},
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        reason = payload.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "MODAL_BUDGET" not in reason


# ---------------------------------------------------------------------------
# Deny-reason content
# ---------------------------------------------------------------------------


def test_modal_budget_reason_points_at_chat_gates_and_adopt_and_proceed(
    tmp_path, capsys, monkeypatch
):
    """v2.2.1: deny reason points the orchestrator at the chat-based gate
    pattern (Step 6/8/9 of run.md) and the Adopt-and-proceed clause for
    non-gate decisions."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run_at_stage(tmp_path, "execute")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "extra?", "header": "X", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    # Must reference the chat-gate pattern
    assert "chat" in reason.lower()
    # Must name the keyword grammar the operator uses at chat gates
    assert "APPROVE" in reason
    # Must reference the adopt-and-proceed alternative for non-gate decisions
    assert "ADOPT" in reason or "adopt" in reason
    assert "director-decisions" in reason
