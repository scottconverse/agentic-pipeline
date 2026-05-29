# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/run_all.py policy-report.md write behavior (v1.3.1).

Pins the false-stop fix that has run_all.py write the canonical
artifact directly when `--run` is given, instead of relying on the
orchestrator to capture stdout. This was the root cause of the v1.2.1
PROMOTED report's Finding #1: auto_promote read policy-report.md
looking for the marker line, didn't find it (orchestrator's stdout
capture lost it), and stopped on a false policy failure even though
the policy gate actually passed.

Tests use in-process monkeypatch on RUN_DIR_BASE — the real check
scripts still run as subprocesses against the live repo and may PASS
or FAIL depending on the working tree, which is fine. The
artifact-write contract holds regardless of check outcomes.
"""

from __future__ import annotations

from scripts.run_all import main as run_all_main


def test_run_all_writes_policy_report_md_to_run_dir(tmp_path, monkeypatch) -> None:
    """When --run is given and the run dir exists, run_all writes
    `<RUN_DIR_BASE>/<run-id>/policy-report.md` containing the POLICY
    marker line — independent of which checks pass or fail."""
    fake_runs = tmp_path / ".agent-runs"
    run_id = "policy-write-test"
    run_dir = fake_runs / run_id
    run_dir.mkdir(parents=True)

    monkeypatch.setattr("scripts.run_all.RUN_DIR_BASE", fake_runs)
    monkeypatch.setattr("sys.argv", ["run_all.py", "--run", run_id])

    exit_code = run_all_main()
    assert exit_code in (0, 1)

    report = run_dir / "policy-report.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "POLICY:" in text
    assert "Policy checks" in text


def test_run_all_skips_artifact_write_when_run_dir_missing(
    tmp_path, monkeypatch
) -> None:
    """If `--run` is given but the run dir does not exist, the script
    must not silently create it — that would mask a real run-id typo."""
    fake_runs = tmp_path / ".agent-runs"
    fake_runs.mkdir()

    monkeypatch.setattr("scripts.run_all.RUN_DIR_BASE", fake_runs)
    monkeypatch.setattr("sys.argv", ["run_all.py", "--run", "no-such-run"])

    exit_code = run_all_main()
    assert exit_code in (0, 1)
    assert not (fake_runs / "no-such-run").exists()


def test_run_all_skips_artifact_write_without_run(tmp_path, monkeypatch) -> None:
    """Without --run, run_all is a developer convenience; it must not
    attempt to write any .agent-runs path."""
    fake_runs = tmp_path / ".agent-runs"

    monkeypatch.setattr("scripts.run_all.RUN_DIR_BASE", fake_runs)
    monkeypatch.setattr("sys.argv", ["run_all.py"])

    exit_code = run_all_main()
    assert exit_code in (0, 1)
    assert not fake_runs.exists()


# ---------------------------------------------------------------------------
# Pass 3 (audit Cluster C / ENG-003) regressions: v2.0 enforcement wiring
# ---------------------------------------------------------------------------
#
# v2.0 wires the directive / scope-lock / control-loop / readiness /
# decision-ledger checks into run_all.CHECKS. Each is gated on a
# prerequisite artifact in the run dir (CHECK_PREREQUISITES) so v1.x
# runs that don't opt into v2.0 enforcement pass through cleanly.


def test_v20_checks_are_in_checks_list() -> None:
    """The seven v2.0 enforcement scripts must be listed in CHECKS.
    Drift here = the v2.0 enforcement layer is dead code from the
    orchestrator's perspective (audit ENG-003)."""
    from scripts.run_all import CHECKS

    names = {name for name, _args in CHECKS}
    for expected in (
        "check_directive_conformance",
        "check_scope_lock",
        "check_rung_file_ownership",
        "check_release_docs_consistency",
        "check_pipeline_control_loop",
        "check_execute_readiness",
        "check_decision_ledger",
    ):
        assert expected in names, f"v2.0 check `{expected}` missing from run_all.CHECKS"


def test_v20_checks_have_prerequisites_mapped() -> None:
    """Each v2.0 entry in CHECKS must have a prerequisite path mapped
    in CHECK_PREREQUISITES so the conditional-skip pattern works.
    Drift here = v2.0 checks fail loudly on v1.x runs that don't have
    the artifact."""
    from scripts.run_all import CHECK_PREREQUISITES

    for expected, artifact in (
        ("check_directive_conformance", "directive.yaml"),
        ("check_scope_lock", "scope-lock.yaml"),
        ("check_rung_file_ownership", "scope-lock.yaml"),
        ("check_release_docs_consistency", "scope-lock.yaml"),
        ("check_pipeline_control_loop", "active-control-state.md"),
        ("check_execute_readiness", "implementation-report.md"),
        ("check_decision_ledger", "decision-ledger.ndjson"),
    ):
        assert CHECK_PREREQUISITES.get(expected) == artifact, (
            f"prerequisite for {expected} should be {artifact}, "
            f"got {CHECK_PREREQUISITES.get(expected)}"
        )


def test_v20_checks_skip_when_prereq_missing(tmp_path, monkeypatch) -> None:
    """When the run dir exists but the prerequisite artifact is absent,
    the v2.0 check is SKIPPED (counted as PASS) and the report shows
    `[PASS] <check_name>` with `SKIP - <reason>` body. This preserves
    backwards-compat for v1.x runs that don't opt into scope-lock /
    directive / control-loop / etc."""
    fake_runs = tmp_path / ".agent-runs"
    run_id = "v1-run-no-v2-artifacts"
    (fake_runs / run_id).mkdir(parents=True)

    monkeypatch.setattr("scripts.run_all.RUN_DIR_BASE", fake_runs)
    monkeypatch.setattr("sys.argv", ["run_all.py", "--run", run_id])

    exit_code = run_all_main()
    assert exit_code in (0, 1)

    report_text = (fake_runs / run_id / "policy-report.md").read_text(encoding="utf-8")
    # Every v2.0 check should appear with SKIP reason since their
    # prerequisite artifacts don't exist in this fixture.
    for v20_check in (
        "check_directive_conformance",
        "check_scope_lock",
        "check_pipeline_control_loop",
        "check_execute_readiness",
        "check_decision_ledger",
    ):
        assert v20_check in report_text
        # The check should not be reported FAIL when prereq is absent
        check_section_idx = report_text.index(v20_check)
        # Look for [PASS] or [FAIL] before this position, within the
        # same section (preceding 30 chars).
        preceding = report_text[max(0, check_section_idx - 40):check_section_idx]
        assert "[PASS]" in preceding, (
            f"v2.0 check {v20_check} should be PASS (via SKIP) when "
            f"prereq is missing; section preamble was {preceding!r}"
        )


def test_pipeline_payload_run_all_matches_top_level() -> None:
    """The scaffold mirror under skills/pipeline-init/.../scripts/
    run_all.py must stay in lockstep with the top-level scripts/run_all.py.
    Pipeline-init copies the mirror into operator projects; drift means
    new projects get the pre-fix behavior."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    top_level = (repo_root / "scripts" / "run_all.py").read_text(encoding="utf-8")
    mirror = (
        repo_root
        / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "scripts"
        / "run_all.py"
    ).read_text(encoding="utf-8")

    # Both must have the v2.0 wires and the conditional-skip pattern.
    for needle in (
        "CHECK_PREREQUISITES",
        "check_directive_conformance",
        "check_scope_lock",
        "check_pipeline_control_loop",
        "check_execute_readiness",
        "check_decision_ledger",
        "agent-pipeline-claude 3.0.1",
    ):
        assert needle in top_level, f"top-level run_all missing `{needle}`"
        assert needle in mirror, f"pipeline-payload run_all mirror missing `{needle}`"
