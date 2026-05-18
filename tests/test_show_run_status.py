# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_show_run_status.py).

from scripts.show_run_status import summarize_run


def test_show_run_status_summarizes_log_and_control_state(tmp_path) -> None:
    run = tmp_path / "sample-run"
    run.mkdir()
    (run / "run.log").write_text(
        "2026-05-13T00:00:00Z | manifest | COMPLETE | approved\n"
        "2026-05-13T00:01:00Z | execute | BLOCKED | needs approval\n",
        encoding="utf-8",
    )
    (run / "active-control-state.md").write_text(
        "active_run: true\n"
        "current_stage: execute\n"
        "final_response_allowed: false\n"
        "stop_condition: none\n"
        "next_required_action: continue executor\n"
        "continuing_to: execute\n",
        encoding="utf-8",
    )

    summary = "\n".join(summarize_run(run))

    assert "show-run-status: sample-run" in summary
    assert "stages_complete: 1" in summary
    assert "current_stage: execute" in summary
    assert "next_required_action: continue executor" in summary


def test_show_run_status_reports_skipped_malformed_log_lines(tmp_path) -> None:
    run = tmp_path / "sample-run"
    run.mkdir()
    (run / "run.log").write_text(
        "2026-05-13T00:00:00Z | manifest | COMPLETE | approved\n"
        "partially written line from crash\n",
        encoding="utf-8",
    )

    summary = "\n".join(summarize_run(run))

    assert "stages_complete: 1" in summary
    assert "run_log_warning: skipped 1 malformed line(s)" in summary


def test_show_run_status_honors_claude_project_dir_at_call_time(tmp_path, monkeypatch, capsys) -> None:
    """Phase 6.c bug fix (checkpoint H workaround): when the script lives
    in the plugin install cache (no .git ancestor), main() must resolve
    the run dir against CLAUDE_PROJECT_DIR rather than the script's
    parent. _resolve_repo_root() is called inside main() so env changes
    take effect even though REPO_ROOT was baked at import time."""
    runs_root = tmp_path / ".agent-runs"
    run = runs_root / "test-run"
    run.mkdir(parents=True)
    (run / "active-control-state.md").write_text(
        "active_run: true\ncurrent_stage: execute\nfinal_response_allowed: false\nstop_condition: none\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["show_run_status.py", "--run", "test-run"])

    from scripts import show_run_status
    rc = show_run_status.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "show-run-status: test-run" in out
    assert "current_stage: execute" in out
