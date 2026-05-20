#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""v2.2.1: refresh stale scaffolded policy scripts in an initialized project.

When a project is initialized via ``/agent-pipeline-claude:pipeline-init``,
the plugin scaffolds ``scripts/policy/*.py`` into the project root by
copying the plugin's canonical versions at ``<plugin_root>/scripts/policy/``.
After scaffold, those copies are project-owned: subsequent plugin
upgrades do NOT auto-update them. Over time the project's copies drift
from the plugin's canonical, causing policy-stage failures that aren't
work-quality issues but rather scaffold-version mismatches.

Concrete example caught during 2026-05-20 ``python-311-honesty`` run:
the ``github-cleanup-2026-05-18`` project was scaffolded under v2.0
before the v2.1.0 project-shape adapter shipped. Its
``scripts/policy/check_allowed_paths.py`` always invoked ``git diff``
against the umbrella root (which is NOT a git repo for the
multi-repo-admin shape), crashing with exit 129. The v2.1.0 adapter
skips the git-diff for that shape, but the project's copy never
received the upgrade. The python-311-honesty run had to ratify this
as a documented exception (DR-F) rather than rerun the policy stage
cleanly.

This script closes the gap: comparing each project ``scripts/policy/<name>``
against the plugin canonical at ``<plugin_root>/scripts/policy/<name>``
by SHA-256, reporting a per-script status, and optionally overwriting
stale + missing copies with the canonical versions.

Usage::

    # Report only (default; non-destructive):
    python "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_policy_scaffolding.py" \
        --project-root .

    # Apply: overwrite stale + create missing:
    python "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_policy_scaffolding.py" \
        --project-root . --apply

    # Explicit plugin-root (for testing or non-Cowork invocations):
    python /path/to/refresh_policy_scaffolding.py \
        --plugin-root /path/to/plugin \
        --project-root /path/to/project --apply

The script never touches files outside ``<project_root>/scripts/policy/``.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


# What status a project-side script can be in relative to its plugin canonical.
STATUS_IDENTICAL = "identical"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_PROJECT_ONLY = "project_only"  # exists in project, not in plugin

VALID_STATUSES = (STATUS_IDENTICAL, STATUS_STALE, STATUS_MISSING, STATUS_PROJECT_ONLY)


@dataclass(frozen=True)
class ScriptDiff:
    """Per-script comparison record."""

    name: str
    project_path: Path
    plugin_path: Path
    plugin_sha256: str | None
    project_sha256: str | None
    status: str

    @property
    def stale_or_missing(self) -> bool:
        return self.status in (STATUS_STALE, STATUS_MISSING)


def _sha256_of(path: Path) -> str | None:
    """Return hex SHA-256 of file at path, or None if unreadable."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return None


def _resolve_plugin_root(plugin_root: Path | None) -> Path:
    """Resolve plugin_root from arg, env var, or script location."""
    if plugin_root is not None:
        return plugin_root.resolve()
    import os
    env_hint = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_hint:
        return Path(env_hint).expanduser().resolve()
    # This script lives at <plugin_root>/scripts/refresh_policy_scaffolding.py
    # so plugin_root is two levels up.
    return Path(__file__).resolve().parents[1]


def compare_scaffolded_scripts(
    plugin_root: Path, project_root: Path
) -> list[ScriptDiff]:
    """Compare project's ``scripts/policy/*.py`` against plugin canonical.

    Returns a list of ScriptDiff records, one per script that exists in
    EITHER the plugin canonical or the project's scaffolded copy.
    Sorted by script name.
    """
    plugin_dir = plugin_root / "scripts" / "policy"
    project_dir = project_root / "scripts" / "policy"

    plugin_scripts: dict[str, Path] = {}
    if plugin_dir.is_dir():
        for p in sorted(plugin_dir.glob("*.py")):
            # Skip __init__.py, __pycache__, anything not a policy check
            if p.name.startswith("_"):
                continue
            plugin_scripts[p.name] = p

    project_scripts: dict[str, Path] = {}
    if project_dir.is_dir():
        for p in sorted(project_dir.glob("*.py")):
            if p.name.startswith("_"):
                continue
            project_scripts[p.name] = p

    all_names = sorted(set(plugin_scripts.keys()) | set(project_scripts.keys()))
    diffs: list[ScriptDiff] = []
    for name in all_names:
        plugin_path = plugin_scripts.get(name, plugin_dir / name)
        project_path = project_scripts.get(name, project_dir / name)
        plugin_sha = _sha256_of(plugin_path) if name in plugin_scripts else None
        project_sha = _sha256_of(project_path) if name in project_scripts else None

        if name in plugin_scripts and name not in project_scripts:
            status = STATUS_MISSING
        elif name not in plugin_scripts and name in project_scripts:
            status = STATUS_PROJECT_ONLY
        elif plugin_sha is not None and plugin_sha == project_sha:
            status = STATUS_IDENTICAL
        else:
            status = STATUS_STALE

        diffs.append(
            ScriptDiff(
                name=name,
                project_path=project_path,
                plugin_path=plugin_path,
                plugin_sha256=plugin_sha,
                project_sha256=project_sha,
                status=status,
            )
        )
    return diffs


def refresh_scripts(
    diffs: list[ScriptDiff],
) -> list[str]:
    """Apply stale + missing scripts from plugin canonical → project.

    Returns the list of script names that were copied. Project-only
    scripts and identical scripts are left untouched. Each target's
    parent directory is created if missing.
    """
    refreshed: list[str] = []
    for diff in diffs:
        if not diff.stale_or_missing:
            continue
        if diff.plugin_sha256 is None:
            # Plugin canonical missing — should not happen if name is in
            # plugin_scripts, but defensive.
            continue
        try:
            diff.project_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(diff.plugin_path, diff.project_path)
            refreshed.append(diff.name)
        except (OSError, shutil.SameFileError):
            # Best-effort; report what we managed but don't crash.
            continue
    return refreshed


def format_report(diffs: list[ScriptDiff]) -> str:
    """Render a human-readable diff manifest."""
    if not diffs:
        return (
            "No policy scripts found in either plugin canonical or "
            "project. Nothing to compare."
        )
    counts = {
        STATUS_IDENTICAL: 0,
        STATUS_STALE: 0,
        STATUS_MISSING: 0,
        STATUS_PROJECT_ONLY: 0,
    }
    for d in diffs:
        counts[d.status] = counts.get(d.status, 0) + 1
    lines = [
        "Policy script scaffolding diff",
        "==============================",
        "",
        f"  total:        {len(diffs)}",
        f"  identical:    {counts[STATUS_IDENTICAL]}",
        f"  stale:        {counts[STATUS_STALE]}",
        f"  missing:      {counts[STATUS_MISSING]}",
        f"  project-only: {counts[STATUS_PROJECT_ONLY]}",
        "",
    ]
    if counts[STATUS_STALE] or counts[STATUS_MISSING]:
        lines.append("Refresh candidates (run with --apply to refresh):")
        for d in diffs:
            if d.stale_or_missing:
                lines.append(f"  [{d.status:>12}] {d.name}")
        lines.append("")
    if counts[STATUS_PROJECT_ONLY]:
        lines.append(
            "Project-only scripts (kept as-is; the plugin canonical does NOT "
            "ship a version of these):"
        )
        for d in diffs:
            if d.status == STATUS_PROJECT_ONLY:
                lines.append(f"  [project_only] {d.name}")
        lines.append("")
    if counts[STATUS_IDENTICAL]:
        lines.append(
            f"{counts[STATUS_IDENTICAL]} script(s) match the plugin canonical (no refresh needed)."
        )
    if not (counts[STATUS_STALE] or counts[STATUS_MISSING]):
        lines.append("All scripts current. No refresh action required.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare and refresh scaffolded policy scripts."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Project root containing scripts/policy/ to compare.",
    )
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=None,
        help="Override plugin root (defaults to CLAUDE_PLUGIN_ROOT env var "
        "or this script's parent.parent).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Overwrite stale + create missing scripts. Without --apply, "
        "only report the diff.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON diff instead of human-readable report.",
    )
    args = parser.parse_args(argv)

    plugin_root = _resolve_plugin_root(args.plugin_root)
    project_root = args.project_root.resolve()

    diffs = compare_scaffolded_scripts(plugin_root, project_root)

    if args.json:
        import json
        out = {
            "plugin_root": str(plugin_root),
            "project_root": str(project_root),
            "scripts": [
                {
                    "name": d.name,
                    "status": d.status,
                    "plugin_sha256": d.plugin_sha256,
                    "project_sha256": d.project_sha256,
                    "project_path": str(d.project_path),
                    "plugin_path": str(d.plugin_path),
                }
                for d in diffs
            ],
        }
        if args.apply:
            refreshed = refresh_scripts(diffs)
            out["refreshed"] = refreshed
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(format_report(diffs))
        if args.apply:
            refreshed = refresh_scripts(diffs)
            print()
            if refreshed:
                print(f"Refreshed {len(refreshed)} script(s):")
                for name in refreshed:
                    print(f"  - {name}")
            else:
                print("No scripts needed refreshing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
