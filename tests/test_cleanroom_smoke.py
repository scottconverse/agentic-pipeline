"""Cleanroom smoke test — exercises the FIRST skill end-to-end against a real fixture.

This is the test that catches the failure class the load-only cleanroom tests
miss: plugin loads, manifest validates, structure checks pass — but slash
commands either silently no-op or scaffold to the wrong path. That class
would have shipped through every other tier.

Procedure:

1. Copy the plugin source to a tmp dir (no .git, no caches).
2. Copy a tiny fixture project to ANOTHER tmp dir (a fresh greenfield, so
   we don't depend on tests/fixtures/civiccast-shaped/ already being clean).
3. Run `claude --plugin-dir <plugin-tmp> --add-dir <fixture-tmp> -p` from
   the fixture-tmp, driving:
       turn 1: invoke the agent-pipeline-claude:pipeline-init skill
       turn 2: APPROVE
4. Assert the fixture-tmp now contains `.pipelines/` with the expected
   role files + pipeline yamls. That proves the FIRST skill actually
   executed and scaffolded its bundled payload to the right place in a
   real project (not just registered in the slash palette).

Cost: ~$0.05 in Haiku, ~30–60 seconds wall.
Requires: an authenticated `claude` CLI on PATH (it uses whatever auth the CLI
already has — a subscription login or an API key; this test does not care
which). Skips only when `claude` is not on PATH.

This is a SMOKE test, not full E2E. It does NOT run `/run` (the full pipeline
costs ~$2–15 and takes 5–30 minutes — that's tag/nightly territory). Smoke
proves the install + first-skill wire works; full E2E (manual or on-tag)
proves the orchestration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# Reuse the file list from the load-only cleanroom test, but verbatim here
# to avoid coupling the two test modules (each can be deleted independently).
def _project_files_to_copy() -> list[str]:
    return [
        ".claude-plugin",
        "skills",
        "commands",
        "pipelines",
        "scripts",
        "ARCHITECTURE.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "USER-MANUAL.md",
    ]


def _copy_plugin_to(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in _project_files_to_copy():
        src = REPO_ROOT / name
        if not src.exists():
            continue
        dst = dest / name
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    ".agent-runs",
                ),
            )
        else:
            shutil.copy2(src, dst)


def _scaffold_minimal_fixture(dest: Path) -> None:
    """Write a minimum-viable project that gives /pipeline-init enough to chew on.

    The fixture needs to look enough like a project for the orientation step
    to produce a sensible summary, but small enough to assert against
    deterministically. Three files:
      - README.md           (orientation reads it)
      - CLAUDE.md           (project conventions)
      - HANDOFF.md          (the drafter references it if a /run follows)
    Plus an empty docs/ dir so the orientation reports "no ADRs" without erroring.
    """
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "README.md").write_text(
        textwrap.dedent(
            """\
            # Cleanroom smoke fixture

            Minimum-viable project for exercising
            `agent-pipeline-claude:pipeline-init` end-to-end against a fresh
            install. Stack: Python 3.12+, Pytest. No real source code.

            Used by `tests/test_cleanroom_smoke.py`.
            """
        ),
        encoding="utf-8",
    )
    (dest / "CLAUDE.md").write_text(
        textwrap.dedent(
            """\
            # CLAUDE.md — Cleanroom smoke fixture conventions

            Stack: Python 3.12+, Pytest
            Test framework: Pytest
            Lint: Ruff
            Type-check: Mypy

            Conventional Commits + DCO sign-off required.
            """
        ),
        encoding="utf-8",
    )
    (dest / "HANDOFF.md").write_text(
        "# HANDOFF — Cleanroom smoke fixture\n\nNothing in flight. Fresh state.\n",
        encoding="utf-8",
    )
    (dest / "docs").mkdir(exist_ok=True)


def _drive_pipeline_init(
    plugin_dir: Path,
    project_dir: Path,
    session_id: str,
    model: str = "haiku",
) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess]:
    """Two-turn claude -p flow: invoke skill, then APPROVE.

    Returns the two CompletedProcess results.
    """
    claude = shutil.which("claude") or "claude"

    invoke_prompt = (
        'Use the Skill tool to invoke "agent-pipeline-claude:pipeline-init". '
        "Report the orientation step output verbatim."
    )
    turn1 = subprocess.run(
        [
            claude, "-p",
            "--session-id", session_id,
            "--model", model,
            "--plugin-dir", str(plugin_dir),
            invoke_prompt,
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        input="",
    )
    turn2 = subprocess.run(
        [
            claude, "-p",
            "--resume", session_id,
            "--model", model,
            "--plugin-dir", str(plugin_dir),
            "APPROVE",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        input="",
    )
    return turn1, turn2


@pytest.mark.cleanroom_e2e
def test_cleanroom_pipeline_init_scaffolds_into_real_project(tmp_path: Path) -> None:
    """The FIRST skill must actually execute against a fresh project.

    Cleanroom plugin install + cleanroom fixture project + two-turn
    `claude -p --plugin-dir` invocation. After APPROVE, assert that the
    fixture has a `.pipelines/` tree with the expected scaffold.

    This is the smoke test that catches "plugin loads but skills no-op"
    or "skills scaffold to the wrong path" — failure modes the load-only
    cleanroom test cannot see.
    """
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    plugin_dir = tmp_path / "plugin"
    project_dir = tmp_path / "project"
    _copy_plugin_to(plugin_dir)
    _scaffold_minimal_fixture(project_dir)

    sid = str(uuid.uuid4())
    turn1, turn2 = _drive_pipeline_init(plugin_dir, project_dir, sid)

    # The test uses the CLI's existing auth (subscription login or API key). If
    # the CLI is on PATH but not signed in, skip rather than fail — an
    # unauthenticated environment can't reach a model, which is an environment
    # gap, not a regression in the plugin under test.
    if turn1.returncode != 0 and "Please run /login" in (turn1.stdout + turn1.stderr):
        pytest.skip("claude CLI is not logged in (run `claude /login`); live smoke needs an authenticated CLI")

    # Both turns must exit clean.
    assert turn1.returncode == 0, (
        f"turn 1 (invoke) failed: exit={turn1.returncode}\n"
        f"--- stdout ---\n{turn1.stdout}\n--- stderr ---\n{turn1.stderr}"
    )
    assert turn2.returncode == 0, (
        f"turn 2 (APPROVE) failed: exit={turn2.returncode}\n"
        f"--- stdout ---\n{turn2.stdout}\n--- stderr ---\n{turn2.stderr}"
    )

    # The skill must actually scaffold the .pipelines/ directory.
    pipelines = project_dir / ".pipelines"
    assert pipelines.is_dir(), (
        ".pipelines/ was not scaffolded into the project after APPROVE.\n"
        f"--- turn 1 stdout (orientation step) ---\n{turn1.stdout[:2000]}\n"
        f"--- turn 2 stdout (scaffolding step) ---\n{turn2.stdout[:2000]}"
    )

    # The bundled payload should land at least these load-bearing files.
    expected_files = [
        pipelines / "roles" / "manifest-drafter.md",
        pipelines / "roles" / "researcher.md",
        pipelines / "roles" / "planner.md",
        pipelines / "roles" / "executor.md",
        pipelines / "roles" / "verifier.md",
        pipelines / "roles" / "drift-detector.md",
        pipelines / "roles" / "critic.md",
        pipelines / "roles" / "manager.md",
        pipelines / "roles" / "judge.md",
        pipelines / "feature.yaml",
        pipelines / "bugfix.yaml",
        pipelines / "module-release.yaml",
        pipelines / "manifest-template.yaml",
        pipelines / "action-classification.yaml",
        pipelines / "self-classification-rules.md",
    ]
    missing = [str(p.relative_to(project_dir)) for p in expected_files if not p.is_file()]
    assert not missing, (
        f"scaffold incomplete — these files missing from .pipelines/:\n"
        + "\n".join(f"  {m}" for m in missing)
        + f"\n--- actual .pipelines/ tree ---\n"
        + "\n".join(
            f"  {p.relative_to(project_dir)}"
            for p in sorted(pipelines.rglob("*"))
            if p.is_file()
        )
    )

    # scripts/policy/ should also be scaffolded (the 6 stdlib-only checks).
    policy = project_dir / "scripts" / "policy"
    assert policy.is_dir(), "scripts/policy/ was not scaffolded into the project."
    expected_policy = [
        policy / "check_manifest_schema.py",
        policy / "check_allowed_paths.py",
        policy / "check_no_todos.py",
        policy / "check_adr_gate.py",
        policy / "auto_promote.py",
    ]
    missing_policy = [str(p.relative_to(project_dir)) for p in expected_policy if not p.is_file()]
    assert not missing_policy, (
        f"policy scripts missing from scaffold: {missing_policy}"
    )
