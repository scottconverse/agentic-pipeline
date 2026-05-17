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
