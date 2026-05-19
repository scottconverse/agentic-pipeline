# SPDX-License-Identifier: Apache-2.0
"""v2.1.0 modal-budget hook tests.

Enforces the rule that AskUserQuestion modals are permitted only at
declared `gate: human_approval` stages in the pipeline yaml, during an
active non-drafting run. Closes the v2.0.x loophole where the
orchestrator could manufacture mid-execution modals despite the v1.3.0
design that AskUserQuestion gates eliminate the interpretive surface.
"""
from __future__ import annotations

import json
from pathlib import Path

from hooks import hook_runner


def _json_out(capsys):
    out = capsys.readouterr().out.strip()
    assert out, "expected a JSON hook payload, got empty output"
    return json.loads(out)


def _write_feature_pipeline_yaml(root: Path) -> Path:
    """Minimal feature pipeline with 3 human_approval gates."""
    pipelines = root / ".pipelines"
    pipelines.mkdir(parents=True, exist_ok=True)
    yaml_path = pipelines / "feature.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "pipeline: feature",
                "stages:",
                "  - name: manifest",
                "    role: human",
                "    gate: human_approval",
                "  - name: preflight",
                "    role: pipeline",
                "  - name: research",
                "    role: researcher",
                "  - name: plan",
                "    role: planner",
                "    gate: human_approval",
                "  - name: execute",
                "    role: executor",
                "  - name: manager",
                "    role: manager",
                "    gate: human_approval",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return yaml_path


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


def test_modal_budget_allows_modal_at_declared_gate_stage(tmp_path, capsys, monkeypatch):
    """AskUserQuestion at plan stage (a declared gate) is permitted."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_feature_pipeline_yaml(tmp_path)
    _write_active_run_at_stage(tmp_path, "plan")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "approve plan?", "header": "Plan", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny", "modal at declared gate should NOT be denied; got " + str(payload)


def test_modal_budget_denies_modal_between_gate_stages(tmp_path, capsys, monkeypatch):
    """AskUserQuestion at research stage (not a gate) is denied."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_feature_pipeline_yaml(tmp_path)
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
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "MODAL_BUDGET_EXCEEDED" in reason
    assert "research" in reason


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
    """Drafting (intake mid-flight) runs bypass modal budget."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_feature_pipeline_yaml(tmp_path)
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
    _write_feature_pipeline_yaml(tmp_path)
    _write_active_run_at_stage(tmp_path, "research")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "some.txt")},
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    # Whatever classify_tool_risk decides about Read, the deny shouldn't
    # cite MODAL_BUDGET_EXCEEDED:
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        reason = payload.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "MODAL_BUDGET" not in reason


def test_modal_budget_fails_open_when_pipeline_yaml_missing(tmp_path, capsys, monkeypatch):
    """If pipeline yaml can't be resolved, hook fails open (allows)."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # Active run but no .pipelines/feature.yaml at all
    _write_active_run_at_stage(tmp_path, "research")

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "?", "header": "Q", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny", "no pipeline yaml -> fail open expected"


def test_modal_budget_accepts_unknown_stage_with_completed_gate(tmp_path, capsys, monkeypatch):
    """When current_stage is unknown but last_completed_gate is a gate, allow."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_feature_pipeline_yaml(tmp_path)
    run = tmp_path / ".agent-runs" / "modal-budget-run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: (unknown)",
                "last_completed_gate: manager",
                "next_required_action: writing final",
                "stop_condition: none",
                "final_response_allowed: false",
                "continuing_to: complete",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        "pipeline_run:\n  id: modal-budget-run\n  type: feature\n", encoding="utf-8"
    )

    event = {
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{"question": "final?", "header": "Final", "options": []}]
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny"


def test_modal_budget_reason_names_gate_stages(tmp_path, capsys, monkeypatch):
    """Deny reason lists the legitimate gate stage names for the agent."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_feature_pipeline_yaml(tmp_path)
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
    # Must name all three gates so the agent knows what's allowed
    for gate in ("manifest", "plan", "manager"):
        assert gate in reason
    # Must point at the adopt-and-proceed alternative
    assert "ADOPT" in reason or "adopt" in reason
    assert "director-decisions" in reason
