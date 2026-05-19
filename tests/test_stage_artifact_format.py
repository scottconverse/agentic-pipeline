# SPDX-License-Identifier: Apache-2.0
"""v2.1.0 stage-artifact format conformance hook tests.

auto_promote.py requires specific marker lines in verifier/critic/drift
reports. If those markers are missing the run can't auto-promote and
falls back to manual manager gate -- the exact failure pattern that
left the github-cleanup-2026-05-18 run NOT_ELIGIBLE despite all
quality work being clean.

This hook denies Write calls saving any of the three stage reports
inside an active run dir if the inbound content lacks its required
marker. Test matrix covers each artifact's marker pattern, the
in-run-dir gating, and the bypass paths.
"""
from __future__ import annotations

import json
from pathlib import Path

from hooks import hook_runner


def _json_out(capsys):
    out = capsys.readouterr().out.strip()
    assert out, "expected JSON hook payload, got empty output"
    return json.loads(out)


def _setup_active_run(root: Path) -> Path:
    run = root / ".agent-runs" / "format-test-run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: verify",
                "last_completed_gate: plan",
                "next_required_action: write verifier-report",
                "stop_condition: none",
                "final_response_allowed: false",
                "continuing_to: drift-detect",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        "pipeline_run:\n  id: format-test-run\n  type: feature\n",
        encoding="utf-8",
    )
    return run


def test_verifier_report_without_marker_is_denied(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "verifier-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "# Verifier Report\n\nVerifier verdict: PASS. The work is done.\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "STAGE_ARTIFACT_FORMAT_VIOLATION" in reason
    assert "verifier-report.md" in reason
    assert "Criteria" in reason


def test_verifier_report_with_marker_is_allowed(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "verifier-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": (
                "# Verifier Report\n\n"
                "**Criteria: 14 total, 14 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**\n\n"
                "Verifier verdict: PASS.\n"
            ),
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny", "verifier-report with marker should pass; got " + str(payload)


def test_critic_report_without_marker_is_denied(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "critic-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "# Critic Report\n\nNo blockers. Some minor notes.\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "critic-report.md" in payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Findings" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_critic_report_with_marker_is_allowed(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "critic-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": (
                "# Critic Report\n\n"
                "**Findings: 11 total, 0 blocker, 0 critical, 0 major, 11 minor**\n\n"
                "Critic verdict: PASS with minor notes.\n"
            ),
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_drift_report_without_marker_is_denied(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "drift-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "# Drift Report\n\nNo drift detected.\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "drift-report.md" in payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Drift" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_drift_report_with_marker_is_allowed(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "drift-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": (
                "# Drift Report\n\n"
                "**Drift: 3 total, 0 blocker**\n\n"
                "Three minor methodology adaptations resolved in-loop.\n"
            ),
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_artifact_format_skips_files_outside_run_dir(tmp_path, capsys, monkeypatch):
    """A verifier-report.md outside the .agent-runs/<id>/ tree is ignored."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _setup_active_run(tmp_path)
    # Write to a fixture path that LOOKS like a verifier-report but is
    # not inside the run dir
    outside = tmp_path / "docs" / "examples" / "verifier-report.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(outside),
            "content": "# Example doc, no marker required\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        reason = payload.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "STAGE_ARTIFACT_FORMAT_VIOLATION" not in reason


def test_artifact_format_skips_other_filenames(tmp_path, capsys, monkeypatch):
    """intake.md, plan.md, etc. are not in the format-required set."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_run(tmp_path)
    target = run / "intake.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "# Intake (no marker line required)\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        reason = payload.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "STAGE_ARTIFACT_FORMAT_VIOLATION" not in reason


def test_artifact_format_bypassed_without_active_run(tmp_path, capsys, monkeypatch):
    """No active run -> no enforcement."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # NO active run created
    target = tmp_path / "verifier-report.md"
    event = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "# Verifier doodle, no run active\n",
        },
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    out = capsys.readouterr().out.strip()
    if out:
        payload = json.loads(out)
        reason = payload.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "STAGE_ARTIFACT_FORMAT_VIOLATION" not in reason
