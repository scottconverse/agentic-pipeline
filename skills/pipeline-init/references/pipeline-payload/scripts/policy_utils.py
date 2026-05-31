#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Agent Pipeline policy scripts.

Ported from agent-pipeline-codex v0.9.0 (scripts/policy_utils.py).
"""

from __future__ import annotations

import os
import subprocess
import re
import sys
from pathlib import Path


def ensure_utf8_stdout() -> None:
    """Force this process's stdout/stderr to UTF-8 (audit QA-006).

    Several policy scripts print decorative Unicode (—, ✓, →). On a legacy
    Windows console (cp1252) that mojibakes — or, worst case, raises
    UnicodeEncodeError. Reconfiguring to UTF-8 with ``errors="replace"`` makes
    output correct and crash-proof everywhere. Guarded so it is a no-op on
    streams that don't support ``reconfigure`` (pytest capture, an already-
    UTF-8 redirected pipe). Idempotent.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # detached / already-closed stream
            pass


# Apply on import. Every policy script imports this module (for
# find_repo_root), so this single chokepoint makes the whole CLI surface
# UTF-8-safe without an explicit call in each entry point (audit QA-006).
ensure_utf8_stdout()


def find_repo_root(script_file: str) -> Path:
    """Resolve the repo root, preferring the operator's project over the
    plugin install location.

    Resolution order:
      1. ``CLAUDE_PROJECT_DIR`` — set by Cowork (and by the hook layer
         when it spawns subprocesses). In Cowork, the shell ``cwd`` is
         ``.klodock`` rather than the operator's project, so cwd-based
         discovery resolves to the wrong tree; the env var is the
         authoritative pointer.
      2. ``script_dir.parents[1]`` — when the script lives under
         ``<project>/scripts/policy/`` after ``pipeline-init``.
      3. ``git rev-parse --show-toplevel`` from the script's directory —
         the source-tree path used by pytest and by direct CLI
         invocations from inside the plugin repo.
      4. ``script_dir.parent`` — last-resort fallback when no other
         signal is available.

    The Phase 6.c verification round caught only ``show_run_status.py``
    missing the env-var check; this central fix propagates to every
    caller of ``policy_utils.find_repo_root`` (and, by extension, to
    every script that imports it).
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        candidate = Path(env_dir)
        if candidate.is_dir():
            return candidate.resolve()
        # v3.0.1 (audit QA-003/ENG-005): a stale/wrong CLAUDE_PROJECT_DIR was
        # trusted unconditionally, silently pointing every gate at the wrong
        # tree. Reject a value that isn't an existing directory and fall through
        # to git/marker detection rather than validating against nothing.
        print(
            f"policy_utils: CLAUDE_PROJECT_DIR={env_dir!r} is not an existing "
            "directory; ignoring it and falling back to git/marker detection.",
            file=sys.stderr,
        )
    script_dir = Path(script_file).resolve().parent
    if script_dir.name == "policy" and script_dir.parent.name == "scripts":
        return script_dir.parents[1]
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=script_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    return script_dir.parent


def strip_yaml_comment(line: str) -> str:
    """Strip YAML comments without treating # inside quotes as a comment."""
    in_single = False
    in_double = False
    escaped = False

    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1].isspace():
                return line[:index].rstrip()
    return line


def _outside_quotes(line: str) -> str:
    """Return a same-length string with quoted characters replaced by spaces."""
    in_single = False
    in_double = False
    escaped = False
    chars: list[str] = []
    for char in line:
        if escaped:
            chars.append(" ")
            escaped = False
            continue
        if char == "\\" and in_double:
            chars.append(" ")
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            chars.append(" ")
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            chars.append(" ")
            continue
        chars.append(" " if in_single or in_double else char)
    return "".join(chars)


# --- v2.1.0: project-shape adapter ----------------------------------------
#
# Several policy checks (check_allowed_paths, check_no_todos,
# check_scope_lock) assume the canonical project shape: a single-codebase
# git-tracked repo with numeric rung-versioned release plan. Pipeline
# runs against other shapes (multi-repo admin sweeps, library-only
# projects without rungs) hit template-mismatch failures that route the
# run to manual manager-gate instead of auto-promote -- even when the
# work itself is clean.
#
# v2.1.0 introduces an optional `project_shape:` field in SPEC.md (or
# manifest.yaml as a fallback). Recognized shapes:
#
#   single-codebase  (default if unset): existing behavior
#   multi-repo-admin: orchestration root with per-target-repo work
#                     under _repos/<name>/; no umbrella git repo;
#                     non-numeric rung names allowed.
#   library:          a single git repo with no rung-versioned release
#                     plan; treats SPEC as canonical rung-equivalent.
#
# Scripts call read_project_shape(repo_root) and branch their logic
# accordingly. Unknown values default to single-codebase (back-compat).

_PROJECT_SHAPE_VALUES = frozenset(
    {"single-codebase", "multi-repo-admin", "library"}
)


def read_project_shape(repo_root: Path) -> str:
    """Return the project_shape declared in SPEC.md or manifest.yaml.

    Resolution order:
      1. SPEC.md at repo root -- looks for a top-level line
         `project_shape: <value>` (case-insensitive on the key).
      2. Any manifest.yaml under .agent-runs/<id>/ -- the most recently
         modified one -- looking for `project_shape: <value>` under
         pipeline_run or at the top level.

    Returns the value if recognized, else "single-codebase" (default).
    Never raises -- failures fall back to default.
    """
    candidates: list[Path] = []
    spec = repo_root / "SPEC.md"
    if spec.exists():
        candidates.append(spec)
    runs_dir = repo_root / ".agent-runs"
    if runs_dir.exists():
        try:
            manifests = sorted(
                (p for p in runs_dir.rglob("manifest.yaml") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(manifests[:3])
        except Exception:
            pass
    pattern = re.compile(r"^\s*project_shape:\s*[\"']?([A-Za-z0-9_-]+)", re.IGNORECASE)
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = pattern.match(line)
                if match:
                    value = match.group(1).lower()
                    if value in _PROJECT_SHAPE_VALUES:
                        return value
        except Exception:
            continue
    return "single-codebase"


def is_git_repo(repo_root: Path) -> bool:
    """Cheap check for whether repo_root is a git working tree."""
    return (repo_root / ".git").exists()


def unsupported_yaml_constructs(text: str) -> list[str]:
    """Return unsupported YAML constructs in the constrained manifest format.

    The pipeline manifest parser is intentionally stdlib-only and supports a
    small YAML subset. Rejecting richer YAML features is safer than silently
    misreading them.
    """
    violations: list[str] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = strip_yaml_comment(raw.rstrip())
        if not line.strip():
            continue
        stripped = line.strip()
        unquoted = _outside_quotes(stripped)
        if re_match := re.search(r":\s*[|>]\s*$", stripped):
            violations.append(
                f"line {line_number}: block scalar `{re_match.group(0).strip()}` is unsupported; use a quoted single-line scalar."
            )
        if re.search(r"(^|[\s:\[\{])&[A-Za-z0-9_-]+", unquoted):
            violations.append(
                f"line {line_number}: YAML anchors are unsupported; repeat the value explicitly."
            )
        if re.search(r"(^|[\s:\[\{])\*[A-Za-z0-9_-]+", unquoted):
            violations.append(
                f"line {line_number}: YAML aliases are unsupported; repeat the value explicitly."
            )
        if stripped.startswith("<<:"):
            violations.append(
                f"line {line_number}: YAML merge keys are unsupported; expand the merged values explicitly."
            )
    return violations
