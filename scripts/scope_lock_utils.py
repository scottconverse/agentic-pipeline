#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared scope-lock helpers for rung-aware policy checks.

Ported from agent-pipeline-codex v0.9.0 (scripts/scope_lock_utils.py).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    from policy_utils import find_repo_root, strip_yaml_comment
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.policy_utils import find_repo_root, strip_yaml_comment


RUNG_HEADING = re.compile(
    r"^(?P<hashes>#{1,6})\s*(?:rung\s*)?(?:v)?(?P<rung>\d+(?:\.\d+)*)\s*(?:[-:—–]\s*(?P<title>.+?))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReleaseRung:
    rung: str
    title: str
    body: str


def normalize_text(value: str) -> str:
    """Normalize text for conservative cross-file comparisons."""
    normalized = value.replace("—", "-").replace("–", "-")
    normalized = re.sub(r"\s+", " ", normalized.strip().lower())
    return normalized


def parse_simple_yaml(path: Path) -> dict[str, object]:
    """Parse the pipeline's small YAML subset into scalars and string lists."""
    fields: dict[str, object] = {}
    current_key: str | None = None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = strip_yaml_comment(raw.rstrip())
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            existing = fields.setdefault(current_key, [])
            if not isinstance(existing, list):
                fields[current_key] = []
                existing = fields[current_key]
            existing.append(stripped[2:].strip().strip("\"'"))
            continue
        current_key = None
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            current_key = key
            fields.setdefault(key, [])
            continue
        if value == "[]":
            fields[key] = []
            continue
        fields[key] = value.strip("\"'")
    return fields


def list_value(fields: dict[str, object], key: str) -> list[str]:
    value = fields.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def scalar_value(fields: dict[str, object], key: str) -> str:
    value = fields.get(key)
    return value if isinstance(value, str) else ""


def load_scope_lock(run_dir: Path, run_id: str) -> tuple[Path, dict[str, object]]:
    path = run_dir / run_id / "scope-lock.yaml"
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path, parse_simple_yaml(path)


def parse_release_plan(path: Path) -> dict[str, ReleaseRung]:
    """Return release-plan rung sections keyed by rung number."""
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    sections: dict[str, ReleaseRung] = {}
    matches: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = RUNG_HEADING.match(line.strip())
        if match:
            matches.append(
                (
                    index,
                    len(match.group("hashes")),
                    match.group("rung"),
                    (match.group("title") or "").strip(),
                )
            )

    for position, (start, level, rung, title) in enumerate(matches):
        end = len(lines)
        for next_start, next_level, _, _ in matches[position + 1 :]:
            if next_level <= level:
                end = next_start
                break
        body = "\n".join(lines[start + 1 : end]).strip()
        sections[rung] = ReleaseRung(rung=rung, title=title, body=body)
    return sections


def find_term_owner(plan: dict[str, ReleaseRung], term: str) -> str:
    needle = normalize_text(term)
    for rung, section in plan.items():
        haystack = normalize_text(f"{section.title}\n{section.body}")
        if needle and needle in haystack:
            return rung
    return ""


def changed_paths(repo_root: Path) -> list[str]:
    """Return changed/untracked files, or HEAD commit files when tree is clean."""
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    paths = [line.strip() for line in tracked.stdout.splitlines() if line.strip()]

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    paths.extend(line.strip() for line in untracked.stdout.splitlines() if line.strip())

    if paths:
        return sorted(set(paths))

    last_commit = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted({line.strip() for line in last_commit.stdout.splitlines() if line.strip()})


def head_commit_subject(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip()


def repo_root() -> Path:
    return find_repo_root(__file__)
