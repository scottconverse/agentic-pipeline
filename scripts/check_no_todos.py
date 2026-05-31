#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: source files in the project must not contain TODO/FIXME/HACK markers.

Treats unfinished work-in-progress markers as a Blocker for release tagging
— they accumulate across rungs and the "later" usually doesn't happen.
Audit findings get queued in `next-cleanup.md` instead.

This check enforces the rule across the project's source directories,
including `scripts/` (audit ENG-011 — the pipeline's own automation is
source too). `tests/` and `docs/` are explicitly excluded — tests
legitimately mark expected TODO regression cases (xfail rationale strings)
and docs reference the markers descriptively. This file itself is excluded
because it necessarily contains the marker words as *detection* strings, not
as work-in-progress markers.

Configure SCAN_ROOTS for your project. Defaults to scanning every directory
under the repo root that contains Python files, excluding tests/, docs/,
.agent-runs/, .pipelines/, and common venv / build / cache dirs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)

# Directories that ARE scanned. If your project's source isn't auto-detected,
# edit this list explicitly (e.g. SCAN_ROOTS = [REPO_ROOT / "src" / "myproject"]).
DEFAULT_EXCLUDED_DIRS = {
    "tests",
    "test",
    "docs",
    ".agent-runs",
    ".pipelines",
    # scripts/ is intentionally NOT excluded (audit ENG-011): the pipeline's
    # own automation is source and must be TODO-gated like any other. This
    # file self-excludes below since its marker words are detection strings.
    # The pipeline-init payload is a byte-for-byte *copy* of scripts/ +
    # pipelines/ (its canonical originals are already scanned), so the mirror
    # tree is excluded to avoid double-scanning and re-flagging this detector's
    # own copied marker literals.
    "pipeline-payload",
    "node_modules",
    ".venv",
    "venv",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    "site-packages",
    # v2.1.0: multi-repo-admin runs clone target repos into _repos/.
    # Those clones are third-party project source; scanning them for
    # TODO markers produces false positives (especially regex patterns
    # that LITERALLY contain "TODO" / "FIXME" as detection strings).
    # Excluded unconditionally because the directory name is reserved
    # for run-managed clones across project shapes.
    "_repos",
}

PATTERN = re.compile(r"\b(TODO|FIXME|HACK)\b", re.IGNORECASE)


def _discover_scan_roots() -> list[Path]:
    """Auto-discover the project source directories to scan.

    Returns directories under the repo root that contain Python files
    and are not in the excluded set. Override by editing this function
    or the DEFAULT_EXCLUDED_DIRS set above for project-specific needs.
    """
    roots: list[Path] = []
    for child in REPO_ROOT.iterdir():
        if not child.is_dir():
            continue
        if child.name in DEFAULT_EXCLUDED_DIRS:
            continue
        if child.name.startswith("."):
            continue
        # Only include if it contains Python files anywhere in its tree.
        if any(child.rglob("*.py")):
            roots.append(child)
    return roots


def main() -> int:
    scan_roots = _discover_scan_roots()
    if not scan_roots:
        print("check_no_todos: no source directories detected. PASS (vacuous).")
        return 0

    self_path = Path(__file__).resolve()
    violations: list[tuple[Path, int, str]] = []
    for root in scan_roots:
        for py_file in root.rglob("*.py"):
            # Skip files under any excluded directory anywhere in the path.
            if any(part in DEFAULT_EXCLUDED_DIRS for part in py_file.parts):
                continue
            # Skip this detector itself — its source carries the marker words
            # as detection strings, not as work-in-progress markers (ENG-011).
            if py_file.resolve() == self_path:
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_num, line in enumerate(text.splitlines(), start=1):
                if PATTERN.search(line):
                    violations.append((py_file.relative_to(REPO_ROOT), line_num, line.rstrip()))

    if violations:
        print("check_no_todos: FAIL")
        print(
            "  TODO/FIXME/HACK markers in project source are blockers per the project's hard rules "
            "(unfinished work goes in next-cleanup.md, not the source tree)."
        )
        print("  Violations:")
        for path, line_num, line_text in violations:
            print(f"    {path.as_posix()}:{line_num}  {line_text}")
        return 1

    scanned = ", ".join(r.name + "/" for r in scan_roots)
    print(f"check_no_todos: PASS — no TODO/FIXME/HACK markers in {scanned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
