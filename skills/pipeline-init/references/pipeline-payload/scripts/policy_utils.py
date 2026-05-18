#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Agent Pipeline policy scripts.

Ported from agent-pipeline-codex v0.9.0 (scripts/policy_utils.py).
"""

from __future__ import annotations

import os
import subprocess
import re
from pathlib import Path


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
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir).resolve()
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
