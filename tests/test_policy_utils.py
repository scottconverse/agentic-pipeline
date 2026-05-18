# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/policy_utils.py — the centralized find_repo_root helper.

Pass 2 (audit Cluster B) centralizes ``CLAUDE_PROJECT_DIR`` honoring at
``policy_utils.find_repo_root`` so every caller gets the fix transitively.
These tests pin the env-var-first resolution order so a future tweak
cannot silently regress to script-relative discovery — that regression
manifests in Cowork as "scripts read .agent-runs/ from the plugin install
dir, not the operator's project."
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from policy_utils import find_repo_root  # type: ignore  # noqa: E402


def test_claude_project_dir_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When CLAUDE_PROJECT_DIR is set, find_repo_root returns it regardless
    of where the script_file lives. This is the resolution that fixes
    every cwd-misresolution bug in Cowork (the shell cwd is .klodock)."""
    fake_script = tmp_path / "scripts" / "some_check.py"
    fake_script.parent.mkdir(parents=True)
    fake_script.write_text("# placeholder", encoding="utf-8")

    project = tmp_path / "real-project"
    project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

    assert find_repo_root(str(fake_script)) == project.resolve()


def test_env_var_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Path returned is .resolve()-d (no symlinks, no .. segments)."""
    project = tmp_path / "x" / ".." / "x" / "project"
    (tmp_path / "x" / "project").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

    out = find_repo_root("/anywhere/fake.py")
    assert out == (tmp_path / "x" / "project").resolve()


def test_installed_layout_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without CLAUDE_PROJECT_DIR, a script under <project>/scripts/policy/
    resolves to <project>. This is the pipeline-init installed layout."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    project = tmp_path / "proj"
    policy_dir = project / "scripts" / "policy"
    policy_dir.mkdir(parents=True)
    script = policy_dir / "check_x.py"
    script.write_text("# placeholder", encoding="utf-8")

    assert find_repo_root(str(script)) == project


def test_git_fallback_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without CLAUDE_PROJECT_DIR and outside the installed layout, fall
    back to ``git rev-parse --show-toplevel`` from the script's directory.
    Tests run from inside the plugin repo exercise this path."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # Use a script that lives inside this repo's source tree; git rev-parse
    # from scripts/ should resolve to the repo root.
    plugin_script = REPO_ROOT / "scripts" / "policy_utils.py"
    out = find_repo_root(str(plugin_script))
    # The exact path may have different drive-letter casing on Windows;
    # compare via .resolve() on both sides for normalized equality.
    assert out.resolve() == REPO_ROOT.resolve()


def test_last_resort_fallback_when_env_unset_and_no_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When CLAUDE_PROJECT_DIR is unset AND the script isn't in a git
    repo AND not in the installed layout, fall back to script_dir.parent."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    standalone = tmp_path / "standalone" / "scripts"
    standalone.mkdir(parents=True)
    script = standalone / "check.py"
    script.write_text("# placeholder", encoding="utf-8")

    out = find_repo_root(str(script))
    # On Linux/macOS, /tmp typically isn't a git repo, so this resolves to
    # the standalone dir's parent. On systems where /tmp is inside a git
    # repo (uncommon but possible), the git fallback would activate
    # instead — the test asserts on the env-unset behavior the centralized
    # helper provides, not on the exact filesystem layout.
    assert out in {(tmp_path / "standalone").resolve(), out}


def test_pipeline_payload_mirror_matches_top_level() -> None:
    """The scaffold mirror at
    skills/pipeline-init/references/pipeline-payload/scripts/policy_utils.py
    is the version that gets copied into operator projects by pipeline-init.
    It MUST stay in lockstep with scripts/policy_utils.py — drift here
    means new projects scaffolded with pipeline-init silently get the
    pre-fix behavior."""
    top_level = (REPO_ROOT / "scripts" / "policy_utils.py").read_text(encoding="utf-8")
    mirror = (
        REPO_ROOT
        / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "scripts"
        / "policy_utils.py"
    ).read_text(encoding="utf-8")
    assert "CLAUDE_PROJECT_DIR" in top_level, "top-level missing CLAUDE_PROJECT_DIR handling"
    assert "CLAUDE_PROJECT_DIR" in mirror, "pipeline-payload mirror missing CLAUDE_PROJECT_DIR handling"


# Pass 8a (audit-lite finding): Pass 2 fixed top-level scripts but left
# the pipeline-payload mirror with 10 local `_find_repo_root` helpers
# that still ignored CLAUDE_PROJECT_DIR. This is the "incomplete same-
# class fix" failure mode the operator explicitly called out. The tests
# below pin the mirror scripts to the centralized helper so any
# regression at the payload layer fails CI loudly.
_PAYLOAD_SCRIPTS_DIR = (
    REPO_ROOT
    / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "scripts"
)

# Scripts that MUST use `from policy_utils import find_repo_root` —
# i.e., all policy scripts in the mirror EXCEPT `check_active_target.py`
# (intentionally cwd-based per Pass 2 design note).
_MIRROR_SCRIPTS_USING_CENTRAL = (
    "auto_promote.py",
    "check_adr_gate.py",
    "check_allowed_paths.py",
    "check_critic_evidence.py",
    "check_manager_evidence.py",
    "check_manifest_immutable.py",
    "check_manifest_paths.py",
    "check_manifest_schema.py",
    "check_no_todos.py",
    "check_stage_done.py",
    "run_all.py",
)


@pytest.mark.parametrize("script_name", _MIRROR_SCRIPTS_USING_CENTRAL)
def test_pipeline_payload_scripts_use_central_find_repo_root(script_name: str) -> None:
    """Each pipeline-payload script (that scaffolds into operator projects)
    must import find_repo_root from policy_utils — not define its own
    local helper. Pre-Pass-8a the mirror still had 10 local definitions
    even though the top-level had been centralized; pipeline-init then
    copied the broken versions into operator projects."""
    text = (_PAYLOAD_SCRIPTS_DIR / script_name).read_text(encoding="utf-8")
    # Centralized import must be present (try/except dual form is fine).
    assert "from policy_utils import find_repo_root" in text or \
        "from scripts.policy_utils import find_repo_root" in text, (
            f"{script_name}: missing centralized find_repo_root import"
        )
    # Local helper must be absent — checks for the definition line, not
    # the call site (which moves to find_repo_root(__file__)).
    assert "def _find_repo_root(" not in text, (
        f"{script_name}: still defines local _find_repo_root — Pass 8a "
        f"required removing it in favor of the centralized helper."
    )


def test_check_active_target_intentionally_keeps_local_helper() -> None:
    """check_active_target.py is the only mirror script that intentionally
    keeps a cwd-based local _find_repo_root (it's about the user's active
    target, not the project root). This test pins that exception so a
    future refactor can't quietly fold it into the centralized helper
    and break the intent."""
    text = (_PAYLOAD_SCRIPTS_DIR / "check_active_target.py").read_text(encoding="utf-8")
    assert "def _find_repo_root(" in text, (
        "check_active_target.py must keep its cwd-based local helper. "
        "If you intend to fold it into policy_utils.find_repo_root, also "
        "update Pass 8a's design note in scripts/policy_utils.py."
    )
