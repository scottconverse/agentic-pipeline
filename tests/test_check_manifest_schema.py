# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/check_manifest_schema.py — the v1.0 schema validator.

The schema validator is the gate every run's manifest hits. Regressions
here break every downstream stage. These tests cover the rule surface
exhaustively and pin the v1.0 error-message shape (field + problem +
current + suggest) so a future tweak to the script doesn't silently
revert to the v0.5.x soup-of-strings output.

Run from the repo root:
    python -m pytest tests/test_check_manifest_schema.py -v
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SCRIPT = REPO_ROOT / "scripts" / "check_manifest_schema.py"
POLICY_UTILS_SCRIPT = REPO_ROOT / "scripts" / "policy_utils.py"


def _make_fixture_run(
    tmp_path: Path,
    manifest_body: str,
    run_id: str = "test-run",
) -> Path:
    """Write a manifest into a fixture .agent-runs/<run-id>/ directory.

    Returns the path so the test can assert on file contents if needed.
    The script resolves REPO_ROOT relative to the script's own location,
    so tests run the script with cwd at tmp_path to redirect .agent-runs/.
    """
    runs_dir = tmp_path / ".agent-runs" / run_id
    runs_dir.mkdir(parents=True)
    manifest_path = runs_dir / "manifest.yaml"
    manifest_path.write_text(textwrap.dedent(manifest_body), encoding="utf-8")
    return manifest_path


def _run_schema(tmp_path: Path, run_id: str = "test-run") -> subprocess.CompletedProcess[str]:
    """Invoke the schema script as a subprocess.

    The script computes REPO_ROOT from its own path; we copy it (and its
    policy_utils sibling, which provides the centralized find_repo_root
    helper) into a tmp_path-rooted layout so .agent-runs/ resolves there.
    """
    # Copy the script + its policy_utils sibling so the import succeeds.
    # check_manifest_schema imports `from policy_utils import find_repo_root`;
    # the centralized helper is the single source of truth that also honors
    # CLAUDE_PROJECT_DIR (audit Pass 2 / Cluster B).
    tmp_scripts = tmp_path / "scripts"
    tmp_scripts.mkdir(exist_ok=True)
    tmp_script = tmp_scripts / "check_manifest_schema.py"
    tmp_script.write_text(SCHEMA_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_scripts / "policy_utils.py").write_text(
        POLICY_UTILS_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    return subprocess.run(
        [sys.executable, str(tmp_script), "--run", run_id],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# --version flag
# ---------------------------------------------------------------------------


class TestVersionFlag:
    """The --version flag returns the v1.0.0 string and exits 0.

    Pinning this lets operators run `python scripts/policy/check_manifest_schema.py
    --version` to confirm their install actually got the v1.0 scripts and
    not stale v0.5.x copies from an earlier /pipeline-init.
    """

    def test_version_reports_1_2_1(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCHEMA_SCRIPT), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "agent-pipeline-claude 1.2.1" in result.stdout


# ---------------------------------------------------------------------------
# No --run argument -> no-op exit 0
# ---------------------------------------------------------------------------


class TestNoOpWithoutRun:
    """Without --run, the script is a no-op and exits 0.

    Lets the script be a drop-in step in a pipeline-runner shell script
    that may or may not always pass a run id (e.g., a dry-run invocation).
    """

    def test_no_run_argument_exits_zero(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCHEMA_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "no --run argument provided" in result.stdout


# ---------------------------------------------------------------------------
# Happy path — fully populated manifest passes
# ---------------------------------------------------------------------------


VALID_MANIFEST = """
    pipeline_run:
      id: "test-run"
      type: feature
      branch: feature/test-run
      goal: "Close QA-005 conflict-409 race when conflicting row is cancelled mid-lookup; ship the schedule-store retry path with a real-Postgres race test that proves serializability."
      allowed_paths:
        - civiccast/schedule/store.py
        - tests/schedule/test_real_postgres.py
      forbidden_paths:
        - civiccast/live/
        - docs/adr/
      non_goals:
        - "TEST-004..009 promotions (Slice 1 Commit 9)"
        - "Operator UI changes (Slice 2)"
      expected_outputs:
        - "civiccast/schedule/store.py: AssetAlreadyPublishedError exception class"
        - "tests/schedule/test_real_postgres.py: race-test for QA-005 retry"
      required_gates:
        - human_approval_manifest
        - human_approval_plan
        - policy_passed
        - tests_passed
        - human_approval_merge
      risk: medium
      rollback_plan: "git revert <commit-sha>; no schema migration; no down-migration."
      definition_of_done: "QA-005 ledger row flips from Major-open to Closed with a cited real-Postgres race test and a store-level retry. Full pytest passes on the working branch; ruff plus mypy clean; 5-lens self-audit clean before push; CI 6/6 green on the new SHA."
      director_notes:
        - "researcher: read docs/research/v04-slice1-design.md QA-005 section first"
      advances_target: "Close QA-005 conflict-409 race"
      authorizing_source: ".agent-workflows/PROJECT_CONTROL_PLANE.md:42"
"""


class TestValidManifestPasses:
    """A fully populated, schema-correct manifest exits 0 with PASS output."""

    def test_passes_clean_manifest(self, tmp_path: Path) -> None:
        _make_fixture_run(tmp_path, VALID_MANIFEST)
        result = _run_schema(tmp_path)
        assert result.returncode == 0, (
            f"Expected exit 0; got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "PASS" in result.stdout
        assert "1.0 schema" in result.stdout


# ---------------------------------------------------------------------------
# Failure cases — each violation surfaces with the v1.0 error shape
# ---------------------------------------------------------------------------


class TestErrorMessageShape:
    """v1.0 shape: every violation prints field + problem + current + suggestion.

    The v0.5.x shape was a single FAIL line followed by a comma-soup of
    violation strings. The orchestrator translated this into chat with a
    raw Python traceback fallback. v1.0's shape is operator-readable
    directly and includes a footer pointing at the file + resume command.
    """

    def test_short_goal_surfaces_with_remediation(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            'goal: "Close QA-005 conflict-409 race when conflicting row is cancelled mid-lookup; ship the schedule-store retry path with a real-Postgres race test that proves serializability."',
            'goal: "fix bug"',
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Manifest validation FAILED" in result.stdout
        assert "Field: goal" in result.stdout
        assert "too short" in result.stdout
        assert "Current: 'fix bug'" in result.stdout
        assert "Suggestion:" in result.stdout
        assert "Edit" in result.stdout and "manifest.yaml" in result.stdout

    def test_forbidden_status_word_in_goal(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            'goal: "Close QA-005 conflict-409 race when conflicting row is cancelled mid-lookup; ship the schedule-store retry path with a real-Postgres race test that proves serializability."',
            'goal: "Get the QA-005 schedule-store retry feature done and ready to ship for v0.4 release tag."',
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        # Should catch 'done' (or 'ready' / 'shippable') -- any of the forbidden words.
        assert "forbidden status word" in result.stdout

    def test_short_definition_of_done_surfaces(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            'definition_of_done: "QA-005 ledger row flips from Major-open to Closed with a cited real-Postgres race test and a store-level retry. Full pytest passes on the working branch; ruff plus mypy clean; 5-lens self-audit clean before push; CI 6/6 green on the new SHA."',
            'definition_of_done: "tests pass"',
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Field: definition_of_done" in result.stdout
        assert "too short" in result.stdout

    def test_empty_expected_outputs(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            """      expected_outputs:
        - "civiccast/schedule/store.py: AssetAlreadyPublishedError exception class"
        - "tests/schedule/test_real_postgres.py: race-test for QA-005 retry"
""",
            "      expected_outputs: []\n",
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Field: expected_outputs" in result.stdout
        assert "empty" in result.stdout.lower()

    def test_empty_non_goals(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            """      non_goals:
        - "TEST-004..009 promotions (Slice 1 Commit 9)"
        - "Operator UI changes (Slice 2)"
""",
            "      non_goals: []\n",
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Field: non_goals" in result.stdout

    def test_empty_rollback_plan(self, tmp_path: Path) -> None:
        manifest = VALID_MANIFEST.replace(
            'rollback_plan: "git revert <commit-sha>; no schema migration; no down-migration."',
            'rollback_plan: ""',
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Field: rollback_plan" in result.stdout

    def test_broad_allowed_path_requires_forbidden(self, tmp_path: Path) -> None:
        # Replace allowed_paths with a top-level prefix; empty forbidden_paths.
        manifest = VALID_MANIFEST.replace(
            """      allowed_paths:
        - civiccast/schedule/store.py
        - tests/schedule/test_real_postgres.py
      forbidden_paths:
        - civiccast/live/
        - docs/adr/
""",
            """      allowed_paths:
        - src/
      forbidden_paths: []
""",
        )
        _make_fixture_run(tmp_path, manifest)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        assert "Field: forbidden_paths" in result.stdout
        assert "broad prefix" in result.stdout


# ---------------------------------------------------------------------------
# Multi-violation case — all problems surface, footer once
# ---------------------------------------------------------------------------


class TestMultipleViolations:
    """When a manifest has multiple violations, each is reported with the
    full v1.0 shape, numbered, and the footer is printed exactly once."""

    def test_all_violations_numbered_with_single_footer(self, tmp_path: Path) -> None:
        # Maximally-broken manifest: short goal, short DoD, empty lists,
        # empty rollback, broad allowed_paths with empty forbidden_paths.
        broken = textwrap.dedent(
            """
            pipeline_run:
              id: "test-run"
              type: feature
              branch: feature/test-run
              goal: "fix bug"
              allowed_paths:
                - src/
              forbidden_paths: []
              non_goals: []
              expected_outputs: []
              required_gates:
                - human_approval_manifest
              risk: medium
              rollback_plan: ""
              definition_of_done: "tests pass"
              director_notes: []
            """
        )
        _make_fixture_run(tmp_path, broken)
        result = _run_schema(tmp_path)
        assert result.returncode == 1
        # Should report >= 6 violations: goal, DoD, expected_outputs,
        # non_goals, rollback_plan, forbidden_paths.
        assert "Violations: " in result.stdout
        # Numbered format [N/M].
        assert "[1/" in result.stdout
        # Footer printed exactly once.
        assert result.stdout.count("Edit ") == 1
        assert "manifest.yaml to fix" in result.stdout
        assert "re-run /run resume" in result.stdout
