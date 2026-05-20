"""v2.2.1: scaffolded policy script refresh tests.

Closes the v2.2.0 framework-gap surfaced by the 2026-05-20 python-311-honesty
run (bound as DR-F): projects scaffolded under an older plugin version
keep their initial copies of ``scripts/policy/*.py`` indefinitely, even
when subsequent plugin upgrades ship corrected versions. The 2026-05-19
github-cleanup project was scaffolded under v2.0 before the v2.1.0
project-shape adapter shipped; its ``check_allowed_paths.py`` crashed on
``git diff`` in the umbrella root (which isn't a git repo for the
multi-repo-admin shape), forcing the run to ratify the failure as a
documented exception rather than rerunning policy cleanly.

The fix: ``scripts/refresh_policy_scaffolding.py`` compares the project's
copies against the plugin canonical by SHA-256 and offers refresh. The
pipeline-init skill calls it during re-init (when the operator picks
``POLICY`` or ``EVERYTHING`` at the re-init chat gate).

This module tests:
  - The compare function (identical / stale / missing / project_only)
  - The refresh function (copies stale + missing, leaves project_only and identical)
  - The CLI entry point (--apply, --json)
  - Edge cases: missing dirs, unreadable files, partial scaffolds
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# The script under test lives at <repo_root>/scripts/refresh_policy_scaffolding.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from refresh_policy_scaffolding import (  # noqa: E402
    STATUS_IDENTICAL,
    STATUS_MISSING,
    STATUS_PROJECT_ONLY,
    STATUS_STALE,
    compare_scaffolded_scripts,
    format_report,
    refresh_scripts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin_layout(plugin_root: Path, scripts: dict[str, str]) -> None:
    """Create plugin_root/scripts/policy/<name>.py with given contents."""
    pol = plugin_root / "scripts" / "policy"
    pol.mkdir(parents=True, exist_ok=True)
    for name, body in scripts.items():
        (pol / name).write_text(body, encoding="utf-8")


def _make_project_layout(project_root: Path, scripts: dict[str, str]) -> None:
    """Create project_root/scripts/policy/<name>.py with given contents."""
    pol = project_root / "scripts" / "policy"
    pol.mkdir(parents=True, exist_ok=True)
    for name, body in scripts.items():
        (pol / name).write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# compare_scaffolded_scripts
# ---------------------------------------------------------------------------


def test_compare_identical_scripts(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    body = "# v2.2.1 canonical\nprint('ok')\n"
    _make_plugin_layout(plugin, {"check_allowed_paths.py": body})
    _make_project_layout(project, {"check_allowed_paths.py": body})
    diffs = compare_scaffolded_scripts(plugin, project)
    assert len(diffs) == 1
    assert diffs[0].name == "check_allowed_paths.py"
    assert diffs[0].status == STATUS_IDENTICAL
    assert diffs[0].plugin_sha256 == diffs[0].project_sha256


def test_compare_stale_script(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_no_todos.py": "# v2.2.1\n"})
    _make_project_layout(project, {"check_no_todos.py": "# v2.0.0\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    assert diffs[0].status == STATUS_STALE
    assert diffs[0].plugin_sha256 != diffs[0].project_sha256


def test_compare_missing_script(tmp_path: Path) -> None:
    """Script exists in plugin canonical but not in project — operator
    must have skipped POLICY at scaffold or the script was added in a
    later plugin version."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_scope_lock.py": "# v2.1.0\n"})
    (project / "scripts" / "policy").mkdir(parents=True)
    diffs = compare_scaffolded_scripts(plugin, project)
    assert diffs[0].status == STATUS_MISSING
    assert diffs[0].plugin_sha256 is not None
    assert diffs[0].project_sha256 is None


def test_compare_project_only_script(tmp_path: Path) -> None:
    """Project has a custom policy script the plugin doesn't ship.
    Refresh must NOT delete it."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    (plugin / "scripts" / "policy").mkdir(parents=True)
    _make_project_layout(project, {"check_custom_repo_rule.py": "# project local\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    assert diffs[0].status == STATUS_PROJECT_ONLY
    assert diffs[0].plugin_sha256 is None
    assert diffs[0].project_sha256 is not None


def test_compare_mixed(tmp_path: Path) -> None:
    """Realistic mix: one identical + one stale + one missing + one project-only."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(
        plugin,
        {
            "check_allowed_paths.py": "# v2.2.1\n",  # identical
            "check_no_todos.py": "# v2.2.1\n",  # stale (project has v2.0.0)
            "check_scope_lock.py": "# v2.2.1\n",  # missing in project
        },
    )
    _make_project_layout(
        project,
        {
            "check_allowed_paths.py": "# v2.2.1\n",
            "check_no_todos.py": "# v2.0.0\n",
            "check_custom_repo_rule.py": "# project local\n",
        },
    )
    diffs = compare_scaffolded_scripts(plugin, project)
    statuses = {d.name: d.status for d in diffs}
    assert statuses["check_allowed_paths.py"] == STATUS_IDENTICAL
    assert statuses["check_no_todos.py"] == STATUS_STALE
    assert statuses["check_scope_lock.py"] == STATUS_MISSING
    assert statuses["check_custom_repo_rule.py"] == STATUS_PROJECT_ONLY


def test_compare_skips_dunder_files(tmp_path: Path) -> None:
    """__init__.py and __pycache__ should be ignored by the comparison."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(
        plugin, {"check_x.py": "# real\n", "__init__.py": "# pkg\n"}
    )
    _make_project_layout(
        project, {"check_x.py": "# real\n", "__init__.py": "# different\n"}
    )
    diffs = compare_scaffolded_scripts(plugin, project)
    names = [d.name for d in diffs]
    assert "check_x.py" in names
    assert "__init__.py" not in names


def test_compare_no_plugin_dir(tmp_path: Path) -> None:
    """Missing plugin dir → returns project-only scripts as PROJECT_ONLY."""
    plugin = tmp_path / "plugin-with-no-policy-dir"
    project = tmp_path / "project"
    _make_project_layout(project, {"check_a.py": "# x\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    assert diffs[0].status == STATUS_PROJECT_ONLY


def test_compare_no_project_dir(tmp_path: Path) -> None:
    """Missing project dir → returns all plugin scripts as MISSING."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project-with-no-scripts"
    _make_plugin_layout(plugin, {"check_a.py": "# x\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    assert diffs[0].status == STATUS_MISSING


def test_compare_both_dirs_missing(tmp_path: Path) -> None:
    """Both dirs missing → empty result."""
    diffs = compare_scaffolded_scripts(
        tmp_path / "no-plugin", tmp_path / "no-project"
    )
    assert diffs == []


# ---------------------------------------------------------------------------
# refresh_scripts
# ---------------------------------------------------------------------------


def test_refresh_copies_stale_scripts(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    canonical_body = "# v2.2.1 canonical\nprint('new')\n"
    _make_plugin_layout(plugin, {"check_x.py": canonical_body})
    _make_project_layout(project, {"check_x.py": "# v2.0.0 stale\nprint('old')\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert refreshed == ["check_x.py"]
    # File contents now match the canonical
    actual = (project / "scripts" / "policy" / "check_x.py").read_text(encoding="utf-8")
    assert actual == canonical_body


def test_refresh_creates_missing_scripts(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_new.py": "# v2.2.1 new check\n"})
    (project / "scripts" / "policy").mkdir(parents=True)
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert refreshed == ["check_new.py"]
    assert (project / "scripts" / "policy" / "check_new.py").is_file()


def test_refresh_creates_missing_parent_dir(tmp_path: Path) -> None:
    """If project's scripts/policy/ doesn't exist, refresh creates it."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_a.py": "# canonical\n"})
    project.mkdir()  # but NO scripts/ subdir
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert refreshed == ["check_a.py"]
    assert (project / "scripts" / "policy" / "check_a.py").is_file()


def test_refresh_leaves_identical_scripts_alone(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    body = "# canonical\n"
    _make_plugin_layout(plugin, {"check_x.py": body})
    _make_project_layout(project, {"check_x.py": body})
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert refreshed == []


def test_refresh_does_not_touch_project_only_scripts(tmp_path: Path) -> None:
    """A custom project-side script the plugin doesn't ship must be preserved."""
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    (plugin / "scripts" / "policy").mkdir(parents=True)
    _make_project_layout(project, {"check_custom.py": "# project local\n"})
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert refreshed == []
    # File still exists, unchanged
    assert (project / "scripts" / "policy" / "check_custom.py").read_text(
        encoding="utf-8"
    ) == "# project local\n"


def test_refresh_mixed_scenario(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(
        plugin,
        {
            "check_a.py": "# v2.2.1\n",
            "check_b.py": "# v2.2.1\n",
            "check_c.py": "# v2.2.1 new\n",
        },
    )
    _make_project_layout(
        project,
        {
            "check_a.py": "# v2.2.1\n",  # identical
            "check_b.py": "# v2.0.0\n",  # stale
            "check_custom.py": "# project local\n",  # project-only
        },
    )
    diffs = compare_scaffolded_scripts(plugin, project)
    refreshed = refresh_scripts(diffs)
    assert set(refreshed) == {"check_b.py", "check_c.py"}
    # Project-only file preserved
    assert (project / "scripts" / "policy" / "check_custom.py").read_text(
        encoding="utf-8"
    ) == "# project local\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_report_only_does_not_modify(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_x.py": "# v2.2.1\n"})
    _make_project_layout(project, {"check_x.py": "# v2.0.0\n"})
    script = _REPO_ROOT / "scripts" / "refresh_policy_scaffolding.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--plugin-root",
            str(plugin),
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "stale" in result.stdout.lower()
    # File content unchanged (no --apply)
    assert (project / "scripts" / "policy" / "check_x.py").read_text(
        encoding="utf-8"
    ) == "# v2.0.0\n"


def test_cli_apply_refreshes(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_x.py": "# v2.2.1\n"})
    _make_project_layout(project, {"check_x.py": "# v2.0.0\n"})
    script = _REPO_ROOT / "scripts" / "refresh_policy_scaffolding.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--plugin-root",
            str(plugin),
            "--project-root",
            str(project),
            "--apply",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Refreshed" in result.stdout
    assert (project / "scripts" / "policy" / "check_x.py").read_text(
        encoding="utf-8"
    ) == "# v2.2.1\n"


def test_cli_json_format(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(plugin, {"check_x.py": "# canonical\n"})
    _make_project_layout(project, {"check_x.py": "# stale\n"})
    script = _REPO_ROOT / "scripts" / "refresh_policy_scaffolding.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--plugin-root",
            str(plugin),
            "--project-root",
            str(project),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert "scripts" in out
    assert out["scripts"][0]["name"] == "check_x.py"
    assert out["scripts"][0]["status"] == STATUS_STALE


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_includes_status_counts(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    _make_plugin_layout(
        plugin,
        {"check_a.py": "# v2.2.1\n", "check_b.py": "# v2.2.1\n"},
    )
    _make_project_layout(
        project, {"check_a.py": "# v2.0.0\n"}
    )  # check_a is stale, check_b is missing
    diffs = compare_scaffolded_scripts(plugin, project)
    report = format_report(diffs)
    assert "stale" in report.lower()
    assert "missing" in report.lower()
    assert "check_a.py" in report
    assert "check_b.py" in report


def test_format_report_clean_state(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    body = "# canonical\n"
    _make_plugin_layout(plugin, {"check_x.py": body})
    _make_project_layout(project, {"check_x.py": body})
    diffs = compare_scaffolded_scripts(plugin, project)
    report = format_report(diffs)
    assert "current" in report.lower() or "no refresh" in report.lower()
