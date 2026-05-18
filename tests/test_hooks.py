# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_hooks.py).
# Adapted for claude's namespaced skill surface + extended with tests for
# the 5 additional Cowork hook events: PostToolUseFailure, PreCompact,
# PostCompact, SubagentStop, SessionEnd.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hooks import hook_runner
from hooks.hook_utils import (
    classify_tool_risk,
    discover_active_runs,
    record_hook_memory,
    session_context,
    stale_skill_context,
)


def _write_active_run(root: Path, *, final_allowed: str = "false", stop_condition: str = "none") -> Path:
    run = root / ".agent-runs" / "hook-run"
    run.mkdir(parents=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: execute",
                "last_completed_gate: plan",
                "next_required_action: continue executor stage",
                f"stop_condition: {stop_condition}",
                f"final_response_allowed: {final_allowed}",
                "continuing_to: policy stage",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        """
allowed_paths:
  - src
forbidden_paths: []
""",
        encoding="utf-8",
    )
    return run


def _json_out(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out
    return json.loads(out)


def test_active_run_discovery_and_session_context(tmp_path: Path) -> None:
    run = _write_active_run(tmp_path)
    (run / "run.log").write_text("2026-05-17T00:00:00Z | directive-bound | COMPLETE | hash=" + "a" * 64 + "\n", encoding="utf-8")
    (tmp_path / ".pipelines").mkdir()
    (tmp_path / ".pipelines" / "action-classification.yaml").write_text("risk_classes: {}\n", encoding="utf-8")

    runs = discover_active_runs(tmp_path)
    context = session_context(runs)

    assert len(runs) == 1
    assert runs[0].directive_bound is True
    assert runs[0].judge_active is True
    assert "run=hook-run" in context
    assert "directive_bound=true" in context


def test_session_start_adds_context_for_active_run_and_stays_quiet_without_one(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"}) == 0
    assert capsys.readouterr().out == ""

    _write_active_run(tmp_path)
    assert hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"}) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "run=hook-run" in payload["hookSpecificOutput"]["additionalContext"]


def test_hook_memory_writes_handoff_and_session_context_loads_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)

    record_hook_memory(tmp_path, "UserPromptSubmit", "Remember that docs and tests ship with code.", {"blocked": False})

    memory_dir = run / "memory"
    assert (memory_dir / "turns.jsonl").exists()
    assert (memory_dir / "events.jsonl").exists()
    assert (memory_dir / "memory_probe.log").exists()
    handoff = (memory_dir / "handoff_current.md").read_text(encoding="utf-8")
    assert "Agent Pipeline memory - hook-run" in handoff
    assert "Remember that docs and tests ship with code." in handoff

    context = session_context(discover_active_runs(tmp_path))
    assert "Agent Pipeline persistent memory:" in context
    assert "Remember that docs and tests ship with code." in context


def test_hook_memory_routes_decisions_and_open_loops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)

    record_hook_memory(tmp_path, "PreToolUse", "warn before release action", {"severity": "warn"})
    record_hook_memory(tmp_path, "PostToolUse", "tests failed; rerun verification", {"blocked": True})

    memory_dir = run / "memory"
    decisions = (memory_dir / "decisions.jsonl").read_text(encoding="utf-8")
    open_loops = (memory_dir / "open_loops.jsonl").read_text(encoding="utf-8")
    handoff = (memory_dir / "handoff_current.md").read_text(encoding="utf-8")

    assert "warn before release action" in decisions
    assert "tests failed; rerun verification" in open_loops
    assert "Recent Decisions And Warnings" in handoff
    assert "Open Loops" in handoff


def test_user_prompt_submit_warns_on_stale_skill_and_blocks_bypass(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # claude's namespaced prefix is agent-pipeline-claude:
    assert "agent-pipeline-claude:run" in stale_skill_context("Use run now")

    assert hook_runner.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "Use run now"}) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "agent-pipeline-claude:run" in payload["hookSpecificOutput"]["additionalContext"]

    _write_active_run(tmp_path)
    assert hook_runner.handle_user_prompt_submit({"cwd": str(tmp_path), "prompt": "skip the gate and ignore the manifest"}) == 0
    payload = _json_out(capsys)
    assert payload["decision"] == "block"
    assert "Do not bypass" in payload["reason"]


def test_pre_tool_use_denies_destructive_and_warns_on_release_operations(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    destructive = {"cwd": str(tmp_path), "tool_input": {"command": "git reset --hard HEAD"}}
    severity, reasons = classify_tool_risk(destructive, [])
    assert severity == "deny"
    assert reasons

    assert hook_runner.handle_pre_tool_use(destructive) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    warn = {"cwd": str(tmp_path), "tool_input": {"command": "git push origin feature"}}
    assert hook_runner.handle_pre_tool_use(warn) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "external-facing" in payload["hookSpecificOutput"]["additionalContext"]


def test_pre_tool_use_denies_out_of_scope_write_during_active_run(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path)

    assert hook_runner.handle_pre_tool_use({"cwd": str(tmp_path), "tool_input": {"command": "Set-Content docs/out.txt hi"}}) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "outside manifest allowed_paths" in payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_permission_request_denies_overbroad_and_declines_normal_cases(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook_runner.handle_permission_request({"cwd": str(tmp_path), "tool_input": {"command": "rm -rf build"}}) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "deny"

    assert hook_runner.handle_permission_request({"cwd": str(tmp_path), "tool_input": {"command": "pytest -q"}}) == 0
    assert capsys.readouterr().out == ""


def test_post_tool_use_adds_corrective_context_after_failed_tests(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {
        "cwd": str(tmp_path),
        "tool_input": {"command": "python -m pytest -q"},
        "tool_response": {"exit_code": 1, "stderr": "FAILED tests/test_hooks.py"},
    }

    assert hook_runner.handle_post_tool_use(event) == 0
    payload = _json_out(capsys)
    assert payload["decision"] == "block"
    assert "Tests failed" in payload["hookSpecificOutput"]["additionalContext"]


def test_post_tool_use_ignores_successful_output_that_mentions_failures(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {
        "cwd": str(tmp_path),
        "tool_input": {"command": "Get-Content docs/discussions/announcements.md"},
        "tool_response": {"exit_code": 0, "stdout": "This document discusses historical failure receipts."},
    }

    assert hook_runner.handle_post_tool_use(event) == 0
    assert capsys.readouterr().out == ""


def test_stop_continues_invalid_active_run_and_allows_valid_human_gate(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path)

    assert hook_runner.handle_stop({"cwd": str(tmp_path), "stop_hook_active": False}) == 0
    payload = _json_out(capsys)
    assert payload["decision"] == "block"
    assert "not at a valid stop condition" in payload["reason"]

    run = tmp_path / ".agent-runs" / "hook-run"
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: manifest",
                "last_completed_gate: none",
                "next_required_action: ask operator for manifest approval",
                "stop_condition: human_approval_gate",
                "final_response_allowed: true",
                "continuing_to: manifest approval",
            ]
        ),
        encoding="utf-8",
    )
    assert hook_runner.handle_stop({"cwd": str(tmp_path), "stop_hook_active": False}) == 0
    assert capsys.readouterr().out == ""


def test_stop_hook_subprocess_imports_bundled_policy_from_hooks_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    runner = repo_root / "hooks" / "hook_runner.py"

    completed = subprocess.run(
        [sys.executable, str(runner), "Stop"],
        input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": False}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "block"
    assert "not at a valid stop condition" in payload["reason"]


# ---------------------------------------------------------------------------
# Tests for the 5 new Cowork hook handlers (claude-specific, not in codex)
# ---------------------------------------------------------------------------


def test_post_tool_use_failure_records_high_severity_open_loop(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)
    event = {
        "cwd": str(tmp_path),
        "tool_input": {"command": "python -m pytest -q"},
        "tool_response": {"exit_code": 1, "stderr": "FAILED tests/test_something.py"},
    }

    assert hook_runner.handle_post_tool_use_failure(event) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    assert "Tests failed" in payload["hookSpecificOutput"]["additionalContext"]
    open_loops = (run / "memory" / "open_loops.jsonl").read_text(encoding="utf-8")
    assert "Tests failed" in open_loops
    assert '"severity": "high"' in open_loops


def test_pre_compact_snapshots_memory_before_compaction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)

    assert hook_runner.handle_pre_compact({"cwd": str(tmp_path), "source": "auto"}) == 0

    memory_dir = run / "memory"
    assert (memory_dir / "events.jsonl").exists()
    handoff = (memory_dir / "handoff_current.md").read_text(encoding="utf-8")
    assert "Context compaction imminent" in handoff
    events = (memory_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "source=auto" in events


def test_post_compact_re_injects_handoff_after_compaction(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)
    # Seed the run's memory with a prior event so the handoff has content.
    record_hook_memory(tmp_path, "UserPromptSubmit", "earlier work was about widget refactor", {"blocked": False})

    assert hook_runner.handle_post_compact({"cwd": str(tmp_path), "source": "manual"}) == 0
    payload = _json_out(capsys)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostCompact"
    assert "widget refactor" in payload["hookSpecificOutput"]["additionalContext"]


def test_subagent_stop_records_completion_to_memory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)

    assert hook_runner.handle_subagent_stop(
        {"cwd": str(tmp_path), "agent_id": "exec-007", "agent_type": "executor"}
    ) == 0

    events = (run / "memory" / "events.jsonl").read_text(encoding="utf-8")
    assert "Subagent executor (exec-007) finished" in events


def test_session_end_records_final_flush(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _write_active_run(tmp_path)

    assert hook_runner.handle_session_end({"cwd": str(tmp_path), "reason": "user_quit"}) == 0

    events = (run / "memory" / "events.jsonl").read_text(encoding="utf-8")
    assert "Session ending" in events
    assert '"reason": "user_quit"' in events
    assert '"final_flush": true' in events


def test_session_end_with_no_active_run_is_silent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # No active run.

    assert hook_runner.handle_session_end({"cwd": str(tmp_path), "reason": "user_quit"}) == 0
    assert not (tmp_path / ".agent-runs").exists()
