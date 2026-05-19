# SPDX-License-Identifier: Apache-2.0
"""v2.1.0 project-shape adapter tests.

Three offending policy checks (check_allowed_paths, check_no_todos,
check_scope_lock) previously assumed a single-codebase git-tracked
rung-versioned project. Pipeline runs against multi-repo-admin shapes
hit template-mismatch failures that defeated auto-promote. The shape
adapter reads project_shape from SPEC.md (fallback manifest.yaml) and
branches policy logic accordingly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is on sys.path when running tests
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from policy_utils import is_git_repo, read_project_shape  # noqa: E402


def test_read_project_shape_defaults_to_single_codebase(tmp_path):
    """No SPEC.md, no manifest -> default single-codebase."""
    assert read_project_shape(tmp_path) == "single-codebase"


def test_read_project_shape_reads_spec_md(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# SPEC: my-project\n\nproject_shape: multi-repo-admin\n\n## Purpose\n", encoding="utf-8"
    )
    assert read_project_shape(tmp_path) == "multi-repo-admin"


def test_read_project_shape_accepts_library(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("project_shape: library\n", encoding="utf-8")
    assert read_project_shape(tmp_path) == "library"


def test_read_project_shape_rejects_unknown_values(tmp_path):
    """Unknown shape values fall back to default (back-compat)."""
    spec = tmp_path / "SPEC.md"
    spec.write_text("project_shape: invented-thing\n", encoding="utf-8")
    assert read_project_shape(tmp_path) == "single-codebase"


def test_read_project_shape_falls_back_to_manifest(tmp_path):
    """No SPEC.md, but a manifest.yaml declares the shape."""
    runs_dir = tmp_path / ".agent-runs" / "some-run"
    runs_dir.mkdir(parents=True)
    (runs_dir / "manifest.yaml").write_text(
        "pipeline_run:\n  id: x\n  project_shape: multi-repo-admin\n", encoding="utf-8"
    )
    assert read_project_shape(tmp_path) == "multi-repo-admin"


def test_read_project_shape_case_insensitive(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("PROJECT_SHAPE: multi-repo-admin\n", encoding="utf-8")
    assert read_project_shape(tmp_path) == "multi-repo-admin"


def test_is_git_repo_detects_dot_git_dir(tmp_path):
    assert not is_git_repo(tmp_path)
    (tmp_path / ".git").mkdir()
    assert is_git_repo(tmp_path)


def test_check_allowed_paths_passes_degraded_in_multi_repo_admin(tmp_path):
    """A non-git multi-repo-admin root passes check_allowed_paths cleanly."""
    spec = tmp_path / "SPEC.md"
    spec.write_text("project_shape: multi-repo-admin\n", encoding="utf-8")
    runs = tmp_path / ".agent-runs" / "shape-run"
    runs.mkdir(parents=True)
    (runs / "manifest.yaml").write_text(
        "pipeline_run:\n  id: shape-run\n  allowed_paths:\n    - some/path/\n",
        encoding="utf-8",
    )
    # No .git directory; non-multi-repo-admin would have crashed on git diff
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_allowed_paths.py"), "--run", "shape-run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, (
        f"expected degraded PASS, got returncode={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "degraded" in result.stdout.lower() or "multi-repo-admin" in result.stdout


def test_check_scope_lock_accepts_non_numeric_rung_in_multi_repo_admin(tmp_path):
    """Non-numeric rung name (e.g. 'github-cleanup') works under multi-repo-admin."""
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# SPEC: my-project\n\nproject_shape: multi-repo-admin\n\n"
        "## github-cleanup-2026-05-18\n\nDoes things to scottconverse/foo and scottconverse/bar.\n"
        "proves: foo and bar consolidation.\n",
        encoding="utf-8",
    )
    runs = tmp_path / ".agent-runs" / "shape-run"
    runs.mkdir(parents=True)
    (runs / "manifest.yaml").write_text(
        "pipeline_run:\n  id: shape-run\n  type: feature\n",
        encoding="utf-8",
    )
    (runs / "scope-lock.yaml").write_text(
        "\n".join(
            [
                'current_rung: "github-cleanup-2026-05-18"',
                'canonical_source: "SPEC.md"',
                'rung_title: "the github cleanup rung"',
                'proves: "foo and bar consolidation"',
                "required_modules:",
                '  - "scottconverse/foo"',
                '  - "scottconverse/bar"',
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_scope_lock.py"), "--run", "shape-run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, (
        f"expected scope_lock PASS for multi-repo-admin shape, got returncode={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_check_scope_lock_still_fails_in_single_codebase_with_non_numeric_rung(tmp_path):
    """In single-codebase shape (default), non-numeric rung still fails (back-compat)."""
    runs = tmp_path / ".agent-runs" / "shape-run"
    runs.mkdir(parents=True)
    (runs / "manifest.yaml").write_text(
        "pipeline_run:\n  id: shape-run\n  type: feature\n", encoding="utf-8"
    )
    (runs / "scope-lock.yaml").write_text(
        "\n".join(
            [
                'current_rung: "non-numeric"',
                'canonical_source: "docs/spec/release-plan.md"',
                'rung_title: "x"',
                'proves: "y"',
            ]
        ),
        encoding="utf-8",
    )
    # Provide an empty release-plan.md so canonical_source resolves but
    # has no numeric rungs the existing parser recognizes.
    plan = tmp_path / "docs" / "spec" / "release-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Release plan\n\nNothing here matches numeric rung headers.\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_scope_lock.py"), "--run", "shape-run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    # Should fail because rung header regex needs numeric versions
    assert result.returncode != 0


def test_check_no_todos_excludes_underscore_repos(tmp_path):
    """Files under _repos/ are not scanned for TODO markers."""
    repos = tmp_path / "_repos" / "third-party"
    repos.mkdir(parents=True)
    bad_file = repos / "rules.py"
    bad_file.write_text("# Detect TODO markers in prompts via regex\npat = r'TODO|FIXME'\n", encoding="utf-8")
    # Also create a real source file with no TODO so the script has
    # something to scan and report PASS on
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text("x = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_no_todos.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, (
        f"check_no_todos should not flag files under _repos/. stdout={result.stdout} stderr={result.stderr}"
    )
    assert "rules.py" not in result.stdout
