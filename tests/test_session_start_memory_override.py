"""v2.2.0: SessionStart memory-rule override block tests.

Pins the contract that ``handle_session_start`` emits an
``additionalContext`` block telling the LLM which user-memory rules
are suspended for the duration of an active pipeline run. This is the
framing layer that complements the v2.1.0 modal-budget hook (the
enforcement layer) and the v2.2.0 policy-recheck hook (the
acknowledgement layer).

Closes Scott's 2026-05-19 diagnosis: ``feedback_no_unilateral_product_
decisions.md`` is a project-agnostic memory rule loaded into every
session, and it directly conflicts with v1.3.0's modal-eliminating
design. The override block tells the LLM that during a pipeline run,
the pipeline's declared gates ARE the ask-or-decide policy and the
broader memory rule does not apply.
"""

from __future__ import annotations

import json
from pathlib import Path

from hooks import hook_runner
from hooks.hook_utils import (
    _parse_memory_override_allowlist,
    memory_override_context,
    discover_active_runs,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _write_active_run(repo_root: Path, *, drafting: bool = False, run_id: str = "memo-run") -> Path:
    """Create a minimal active run under ``<repo_root>/.agent-runs/<run_id>/``."""
    run = repo_root / ".agent-runs" / run_id
    run.mkdir(parents=True, exist_ok=True)
    state_value = "drafting" if drafting else "true"
    (run / "active-control-state.md").write_text(
        "\n".join([
            f"active_run: {state_value}",
            "current_stage: research_done",
            "last_completed_gate: manifest",
            "next_required_action: spawn planner",
            "continuing_to: plan stage",
            "stop_condition: user_explicitly_paused_or_stopped",
            "final_response_allowed: true",
        ]),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text("type: feature\nallowed_paths:\n  - src\n", encoding="utf-8")
    return run


def _write_allowlist_in_project(repo_root: Path, content: str) -> Path:
    """Stage a project-local memory-scope-allowlist.yaml.

    Project-local takes precedence over the plugin-source canonical
    copy, per ``_memory_override_allowlist_path``'s resolution order.
    """
    pipelines = repo_root / ".pipelines"
    pipelines.mkdir(parents=True, exist_ok=True)
    allowlist = pipelines / "memory-scope-allowlist.yaml"
    allowlist.write_text(content, encoding="utf-8")
    return allowlist


def _write_user_memory_file(memory_dir: Path, filename: str, body: str = "stub\n") -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / filename
    path.write_text(body, encoding="utf-8")
    return path


def _set_user_memory_dir(monkeypatch, memory_dir: Path) -> None:
    monkeypatch.setenv("CLAUDE_USER_MEMORY_DIR", str(memory_dir))


def _json_out(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out, "expected SessionStart to emit JSON payload"
    return json.loads(out)


# ---------------------------------------------------------------------------
# memory_override_context — direct tests
# ---------------------------------------------------------------------------


def test_no_active_run_returns_empty(tmp_path: Path) -> None:
    assert memory_override_context(tmp_path, []) == ""


def test_drafting_only_runs_return_empty(tmp_path: Path, monkeypatch) -> None:
    _write_active_run(tmp_path, drafting=True)
    runs = discover_active_runs(tmp_path)
    assert runs, "fixture should have discovered the drafting run"
    assert memory_override_context(tmp_path, runs) == ""


def test_no_allowlist_returns_empty(tmp_path: Path, monkeypatch) -> None:
    _write_active_run(tmp_path)
    _set_user_memory_dir(monkeypatch, tmp_path / "memory")
    runs = discover_active_runs(tmp_path)
    # No .pipelines/memory-scope-allowlist.yaml in the project; the
    # plugin-source allowlist may exist but the test runs from the
    # plugin checkout. Use a controlled fixture: stage an empty allowlist.
    _write_allowlist_in_project(tmp_path, "memory_overrides: []\n")
    assert memory_override_context(tmp_path, runs) == ""


def test_allowlist_lists_unresolvable_file_returns_empty(tmp_path: Path, monkeypatch) -> None:
    _write_active_run(tmp_path)
    _set_user_memory_dir(monkeypatch, tmp_path / "no-such-memory-dir")
    _write_allowlist_in_project(
        tmp_path,
        'memory_overrides:\n  - file: nonexistent.md\n    reason: "n/a"\n',
    )
    runs = discover_active_runs(tmp_path)
    assert memory_override_context(tmp_path, runs) == ""


def test_resolved_file_produces_override_block(tmp_path: Path, monkeypatch) -> None:
    _write_active_run(tmp_path, run_id="r-resolve")
    memory_dir = tmp_path / "user-memory"
    _write_user_memory_file(memory_dir, "feedback_no_unilateral_product_decisions.md")
    _set_user_memory_dir(monkeypatch, memory_dir)
    _write_allowlist_in_project(
        tmp_path,
        'memory_overrides:\n'
        '  - file: feedback_no_unilateral_product_decisions.md\n'
        '    reason: "Suspended during pipeline runs."\n',
    )
    runs = discover_active_runs(tmp_path)
    block = memory_override_context(tmp_path, runs)
    assert block, "expected override block to be emitted"
    assert "MEMORY OVERRIDES FOR THIS PIPELINE RUN" in block
    assert "r-resolve" in block, "block should reference the active run id"
    assert "feedback_no_unilateral_product_decisions.md" in block
    assert "Suspended during pipeline runs." in block
    # Backstop reference for the LLM
    assert "modal-budget hook" in block
    assert "policy-recheck hook" in block


def test_override_block_names_resolved_path(tmp_path: Path, monkeypatch) -> None:
    _write_active_run(tmp_path)
    memory_dir = tmp_path / "user-memory"
    resolved = _write_user_memory_file(
        memory_dir, "feedback_no_unilateral_product_decisions.md"
    )
    _set_user_memory_dir(monkeypatch, memory_dir)
    _write_allowlist_in_project(
        tmp_path,
        'memory_overrides:\n'
        '  - file: feedback_no_unilateral_product_decisions.md\n'
        '    reason: "x"\n',
    )
    runs = discover_active_runs(tmp_path)
    block = memory_override_context(tmp_path, runs)
    assert str(resolved) in block, "expected absolute path of resolved file"


# ---------------------------------------------------------------------------
# Allowlist parser tests
# ---------------------------------------------------------------------------


def test_parser_handles_basic_entry(tmp_path: Path) -> None:
    p = tmp_path / "allowlist.yaml"
    p.write_text(
        'memory_overrides:\n'
        '  - file: feedback_x.md\n'
        '    reason: "short reason"\n',
        encoding="utf-8",
    )
    entries = _parse_memory_override_allowlist(p)
    assert entries == [{"file": "feedback_x.md", "reason": "short reason"}]


def test_parser_handles_multiple_entries(tmp_path: Path) -> None:
    p = tmp_path / "allowlist.yaml"
    p.write_text(
        "memory_overrides:\n"
        "  - file: a.md\n"
        '    reason: "alpha"\n'
        "  - file: b.md\n"
        '    reason: "beta"\n',
        encoding="utf-8",
    )
    entries = _parse_memory_override_allowlist(p)
    assert entries == [
        {"file": "a.md", "reason": "alpha"},
        {"file": "b.md", "reason": "beta"},
    ]


def test_parser_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "allowlist.yaml"
    p.write_text(
        "# header comment\n"
        "\n"
        "memory_overrides:\n"
        "# inline comment\n"
        "  - file: x.md\n"
        '    reason: "skip me"\n'
        "\n",
        encoding="utf-8",
    )
    entries = _parse_memory_override_allowlist(p)
    assert entries == [{"file": "x.md", "reason": "skip me"}]


def test_parser_strips_quotes(tmp_path: Path) -> None:
    p = tmp_path / "allowlist.yaml"
    p.write_text(
        "memory_overrides:\n"
        '  - file: "quoted.md"\n'
        "    reason: 'single-quoted reason'\n",
        encoding="utf-8",
    )
    entries = _parse_memory_override_allowlist(p)
    assert entries == [{"file": "quoted.md", "reason": "single-quoted reason"}]


def test_parser_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert _parse_memory_override_allowlist(tmp_path / "nope.yaml") == []


def test_parser_returns_empty_for_empty_section(tmp_path: Path) -> None:
    p = tmp_path / "allowlist.yaml"
    p.write_text("memory_overrides: []\n", encoding="utf-8")
    assert _parse_memory_override_allowlist(p) == []


# ---------------------------------------------------------------------------
# handle_session_start integration tests
# ---------------------------------------------------------------------------


def test_session_start_no_run_no_payload(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_session_start_active_run_no_override_emits_session_context_only(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_USER_MEMORY_DIR", raising=False)
    _write_active_run(tmp_path, run_id="basic-run")
    # Project-local empty allowlist to override any user-side global
    # allowlist the developer machine might have.
    _write_allowlist_in_project(tmp_path, "memory_overrides: []\n")
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    payload = _json_out(capsys)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    ctx = out["additionalContext"]
    assert "Agent Pipeline active run context" in ctx
    assert "basic-run" in ctx
    assert "MEMORY OVERRIDES" not in ctx


def test_session_start_with_resolved_override_includes_block(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path, run_id="memo-active")
    memory_dir = tmp_path / "user-mem"
    _write_user_memory_file(
        memory_dir, "feedback_no_unilateral_product_decisions.md",
        body="---\nname: test memory\n---\nbody\n",
    )
    _set_user_memory_dir(monkeypatch, memory_dir)
    _write_allowlist_in_project(
        tmp_path,
        'memory_overrides:\n'
        '  - file: feedback_no_unilateral_product_decisions.md\n'
        '    reason: "Suspended for this run."\n',
    )
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    payload = _json_out(capsys)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    # Both pieces emitted in a single additionalContext block:
    assert "Agent Pipeline active run context" in ctx
    assert "MEMORY OVERRIDES FOR THIS PIPELINE RUN" in ctx
    assert "memo-active" in ctx
    assert "feedback_no_unilateral_product_decisions.md" in ctx
    assert "Suspended for this run." in ctx


def test_session_start_drafting_run_does_not_emit_override(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path, drafting=True, run_id="draft-run")
    memory_dir = tmp_path / "user-mem"
    _write_user_memory_file(
        memory_dir, "feedback_no_unilateral_product_decisions.md"
    )
    _set_user_memory_dir(monkeypatch, memory_dir)
    _write_allowlist_in_project(
        tmp_path,
        'memory_overrides:\n'
        '  - file: feedback_no_unilateral_product_decisions.md\n'
        '    reason: "x"\n',
    )
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    payload = _json_out(capsys)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    # Drafting context still emits the session header (because the run
    # is intake-staged) but NOT the memory override block.
    assert "Agent Pipeline active run context" in ctx
    assert "MEMORY OVERRIDES" not in ctx


def test_session_start_source_not_in_whitelist_does_nothing(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_active_run(tmp_path)
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "garbage"})
    assert rc == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# CHANGELOG / contract pins
# ---------------------------------------------------------------------------


def test_canonical_allowlist_includes_feedback_no_unilateral() -> None:
    """The shipped pipelines/memory-scope-allowlist.yaml MUST list the
    ad-hoc feedback file that motivated the fix. This catches accidental
    removal during refactors."""
    allowlist = Path(__file__).resolve().parents[1] / "pipelines" / "memory-scope-allowlist.yaml"
    assert allowlist.exists(), "canonical allowlist missing from pipelines/"
    entries = _parse_memory_override_allowlist(allowlist)
    files = [e.get("file") for e in entries]
    assert "feedback_no_unilateral_product_decisions.md" in files
