# SPDX-License-Identifier: Apache-2.0
"""Tests for auto_promote.py::_check_tests vacuous-pass behavior (v1.3.1).

Pins the false-stop fix that lets docs-only runs auto-promote instead
of falsely failing condition 6 on an implementation-report.md that
cannot exist because no implementation stage ran.

The fix only widens condition 6 in the narrow case where the manifest
EXPLICITLY forbids the test directory (forbidden_paths covers tests/
or test/). For runs that *should* have produced an
implementation-report but didn't, the existing strict failure path
remains so real implementation gaps aren't silently auto-promoted.
"""

from __future__ import annotations

from pathlib import Path

from scripts.auto_promote import _check_tests, _manifest_forbids_tests


def test_check_tests_passes_vacuously_when_manifest_forbids_tests_dir(
    tmp_path: Path,
) -> None:
    """Docs-only / tests-out-of-scope run: implementation-report.md
    absent + manifest forbids tests/ → condition 6 passes vacuously."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  goal: "narrate the docs-only change"\n'
        '  allowed_paths:\n'
        '    - "docs/"\n'
        '  forbidden_paths:\n'
        '    - "tests/"\n'
        '    - ".github/workflows/"\n',
        encoding="utf-8",
    )

    result = _check_tests(run_dir)

    assert result.passed
    assert "out of scope" in result.evidence
    assert "tests/" in result.evidence


def test_check_tests_passes_vacuously_when_manifest_forbids_test_singular(
    tmp_path: Path,
) -> None:
    """Same vacuous-pass should also fire on `test/` (singular)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  goal: "narrate docs"\n'
        '  forbidden_paths:\n'
        '    - "test/"\n',
        encoding="utf-8",
    )

    result = _check_tests(run_dir)

    assert result.passed
    assert "out of scope" in result.evidence


def test_check_tests_still_fails_when_no_manifest_and_no_implementation_report(
    tmp_path: Path,
) -> None:
    """Strict default preserved: missing implementation-report with no
    manifest still fails condition 6, so a real implementation gap is
    not silently auto-promoted."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    result = _check_tests(run_dir)

    assert not result.passed
    assert "missing" in result.evidence


def test_check_tests_still_fails_when_manifest_does_not_forbid_tests(
    tmp_path: Path,
) -> None:
    """If the manifest does not forbid test dirs, condition 6 still
    requires a real test-pass signal — the vacuous-pass widens nothing
    outside the explicit declaration."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  goal: "make a code change"\n'
        '  allowed_paths:\n'
        '    - "src/"\n'
        '  forbidden_paths:\n'
        '    - ".github/workflows/"\n',
        encoding="utf-8",
    )

    result = _check_tests(run_dir)

    assert not result.passed
    assert "missing" in result.evidence


def test_check_tests_uses_actual_report_when_implementation_report_exists(
    tmp_path: Path,
) -> None:
    """Vacuous-pass only triggers when implementation-report.md is
    ABSENT. When it exists, the existing pass/fail logic runs
    regardless of forbidden_paths content."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  goal: "narrate"\n'
        '  forbidden_paths:\n'
        '    - "tests/"\n',
        encoding="utf-8",
    )
    (run_dir / "implementation-report.md").write_text(
        "pytest output: 5 passed, 0 failed\n",
        encoding="utf-8",
    )

    result = _check_tests(run_dir)

    assert result.passed
    assert "vacuous" not in result.evidence
    assert "test-pass signal" in result.evidence


def test_manifest_forbids_tests_helper_recognizes_nested_test_paths(
    tmp_path: Path,
) -> None:
    """A forbidden_path of `tests/unit/` should still trigger the
    vacuous-pass — anything under tests/ counts as the test dir."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  forbidden_paths:\n'
        '    - "tests/unit/"\n',
        encoding="utf-8",
    )

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert forbids
    assert "tests/unit/" in matches


def test_manifest_forbids_tests_helper_handles_missing_manifest(
    tmp_path: Path,
) -> None:
    """No manifest at all → return (False, []) so callers fall through
    to the strict default."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert not forbids
    assert matches == []


# Parser-robustness tests — guard against future "simplifications" of
# _manifest_forbids_tests that would regress edge cases the live code
# currently handles correctly.


def test_manifest_forbids_tests_no_false_positive_on_tests_substring(
    tmp_path: Path,
) -> None:
    """`tests-data/` is NOT a test directory and must not trigger
    vacuous-pass. Naive `value.startswith("tests")` would match this."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  forbidden_paths:\n'
        '    - "tests-data/"\n'
        '    - "testlab/"\n',
        encoding="utf-8",
    )

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert not forbids
    assert matches == []


def test_manifest_forbids_tests_handles_inline_empty_list(tmp_path: Path) -> None:
    """`forbidden_paths: []` (inline empty) returns (False, []) — empty
    list, nothing to forbid."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  forbidden_paths: []\n',
        encoding="utf-8",
    )

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert not forbids
    assert matches == []


def test_manifest_forbids_tests_handles_inline_list_form(tmp_path: Path) -> None:
    """Flow-style `forbidden_paths: ["tests/"]` must be detected — this
    is a valid YAML form a manifest writer may use. The block-style
    parser alone would miss this."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  forbidden_paths: ["tests/", ".github/workflows/"]\n',
        encoding="utf-8",
    )

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert forbids
    assert "tests/" in matches


def test_manifest_forbids_tests_handles_inline_comment_after_entry(
    tmp_path: Path,
) -> None:
    """`- "tests/"  # explanation` must strip the comment and detect
    the entry."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  forbidden_paths:\n'
        '    - "tests/"  # docs-only run, tests intentionally out of scope\n',
        encoding="utf-8",
    )

    forbids, matches = _manifest_forbids_tests(run_dir)

    assert forbids
    assert "tests/" in matches


def test_auto_promote_main_promotes_docs_only_run_end_to_end(
    tmp_path, monkeypatch
) -> None:
    """Integration test: a fully-scaffolded docs-only run dir
    (manifest forbids tests, all other reports green, no
    implementation-report.md) must drive auto_promote.main() to PROMOTE.
    This is the exact failure-pattern v1.3.1 fixes."""
    from scripts.auto_promote import main as auto_promote_main

    run_id = "docs-only-integration"
    runs_base = tmp_path / ".agent-runs"
    run_dir = runs_base / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.yaml").write_text(
        'pipeline_run:\n'
        '  goal: "Narrow macOS support claims across user-facing docs."\n'
        '  allowed_paths:\n'
        '    - "docs/"\n'
        '    - "README.md"\n'
        '  forbidden_paths:\n'
        '    - "tests/"\n',
        encoding="utf-8",
    )
    (run_dir / "verifier-report.md").write_text(
        "**Criteria: 2 total, 2 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**",
        encoding="utf-8",
    )
    (run_dir / "critic-report.md").write_text(
        "**Findings: 1 total, 0 blocker, 0 critical, 1 major, 0 minor**",
        encoding="utf-8",
    )
    (run_dir / "drift-report.md").write_text(
        "**Drift: 0 total, 0 blocker**",
        encoding="utf-8",
    )
    (run_dir / "policy-report.md").write_text(
        "POLICY: ALL CHECKS PASSED",
        encoding="utf-8",
    )
    # implementation-report.md is INTENTIONALLY absent.

    monkeypatch.setattr("scripts.auto_promote.RUN_DIR_BASE", runs_base)
    monkeypatch.setattr("sys.argv", ["auto_promote.py", "--run", run_id])

    assert auto_promote_main() == 0
    decision = (run_dir / "manager-decision.md").read_text(encoding="utf-8")
    assert "**Decision: PROMOTE**" in decision
    assert "vacuous" not in decision.lower() or "out of scope" in decision.lower()


# ---------------------------------------------------------------------------
# v3.0.0 (Opus 4.8 retarget) — tolerant + structured count parsing.
#
# A more capable, more verbose model under effort=high may re-punctuate,
# reorder, or restyle a count line. These guard the three resolver tiers:
# legacy exact line still parses (back-compat), an explicit machine verdict
# block is honored, tolerant prose still parses, MET vs NOT MET is never
# confused, and unparseable/inconsistent input still fails closed.
# ---------------------------------------------------------------------------

from scripts.auto_promote import _check_critic, _check_drift, _check_verifier


def _w(run_dir: Path, name: str, text: str) -> None:
    (run_dir / name).write_text(text, encoding="utf-8")


def test_verifier_legacy_exact_line_still_parses(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "verifier-report.md",
       "**Criteria: 10 total, 8 MET, 0 PARTIAL, 0 NOT MET, 2 NOT APPLICABLE**")
    r = _check_verifier(run_dir)
    assert r.passed
    assert "exact count line" in r.evidence


def test_verifier_machine_verdict_block_preferred(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "verifier-report.md",
       "Prose with no recognizable count line whatsoever.\n"
       "<!-- PIPELINE-VERDICT:verifier\n"
       "total: 6\nmet: 6\npartial: 0\nnot_met: 0\nnot_applicable: 0\n-->\n")
    r = _check_verifier(run_dir)
    assert r.passed
    assert "machine verdict block" in r.evidence


def test_verifier_tolerant_scan_handles_rephrased_line(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # bold-colon variant, em-dash, extra spaces, NA moved earlier
    _w(run_dir, "verifier-report.md",
       "**Criteria:**  10 total —  2 NOT APPLICABLE,  8 MET,  0 PARTIAL,  0 NOT MET")
    r = _check_verifier(run_dir)
    assert r.passed
    assert "tolerant count scan" in r.evidence


def test_verifier_tolerant_does_not_confuse_met_and_not_met(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # NOT MET listed before MET; must read met=8 (consistent) and not_met=2
    _w(run_dir, "verifier-report.md",
       "Criteria: 10 total, 2 NOT MET, 8 MET, 0 PARTIAL, 0 NOT APPLICABLE")
    r = _check_verifier(run_dir)
    assert not r.passed
    assert "2 NOT MET" in r.evidence  # not "8 NOT MET" — proves no confusion


def test_critic_tolerant_scan_rephrased(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "critic-report.md",
       "Findings — 5 total: 0 blocker, 0 critical, 3 major, 2 minor")
    r = _check_critic(run_dir)
    assert r.passed
    assert "tolerant count scan" in r.evidence


def test_critic_machine_verdict_block(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "critic-report.md",
       "<!-- PIPELINE-VERDICT:critic\n"
       "total: 2\nblocker: 0\ncritical: 0\nmajor: 1\nminor: 1\n-->")
    r = _check_critic(run_dir)
    assert r.passed
    assert "machine verdict block" in r.evidence


def test_drift_tolerant_and_machine_block(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "drift-report.md", "Drift: 3 total, 0 blocker")
    assert _check_drift(run_dir).passed
    _w(run_dir, "drift-report.md",
       "<!-- PIPELINE-VERDICT:drift\ntotal: 0\nblocker: 0\n-->")
    r = _check_drift(run_dir)
    assert r.passed
    assert "machine verdict block" in r.evidence


def test_verifier_unparseable_report_fails_closed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "verifier-report.md",
       "The verifier looked at everything and it all seemed fine to me.")
    r = _check_verifier(run_dir)
    assert not r.passed
    assert "no parseable Criteria counts" in r.evidence


def test_critic_inconsistent_counts_fail(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _w(run_dir, "critic-report.md",
       "**Findings: 5 total, 0 blocker, 0 critical, 1 major, 1 minor**")  # 5 != 2
    r = _check_critic(run_dir)
    assert not r.passed
    assert "inconsistent" in r.evidence
