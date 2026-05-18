#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: release docs must not assign current rung to future-rung work.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_release_docs_consistency.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scope_lock_utils import (
        find_term_owner,
        list_value,
        load_scope_lock,
        normalize_text,
        parse_release_plan,
        repo_root,
        scalar_value,
    )
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.scope_lock_utils import (
        find_term_owner,
        list_value,
        load_scope_lock,
        normalize_text,
        parse_release_plan,
        repo_root,
        scalar_value,
    )


REPO_ROOT = repo_root()
RUN_DIR = REPO_ROOT / ".agent-runs"
DOC_GLOBS = ("README.md", "CHANGELOG.md", "docs/**/*.md", "docs/**/*.html")


def _doc_paths(root: Path, canonical_source: str) -> list[Path]:
    paths: set[Path] = set()
    canonical = (root / canonical_source).resolve()
    for glob in DOC_GLOBS:
        for path in root.glob(glob):
            if path.is_file() and path.resolve() != canonical:
                paths.add(path)
    return sorted(paths)


def evaluate_release_docs_consistency(
    run_id: str,
    run_dir: Path = RUN_DIR,
    root: Path = REPO_ROOT,
) -> list[str]:
    try:
        _, lock = load_scope_lock(run_dir, run_id)
    except FileNotFoundError as exc:
        return [f"scope-lock.yaml missing at {exc.args[0]}."]

    current_rung = scalar_value(lock, "current_rung")
    rung_title = scalar_value(lock, "rung_title")
    canonical_source = scalar_value(lock, "canonical_source")
    plan_path = root / canonical_source
    plan = parse_release_plan(plan_path) if plan_path.exists() else {}
    forbidden_terms = list_value(lock, "forbidden_feature_terms_without_replan")
    rung_markers = [f"v{current_rung}", current_rung]
    violations: list[str] = []

    for path in _doc_paths(root, canonical_source):
        for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            line = normalize_text(raw)
            if not any(marker in line for marker in rung_markers):
                continue
            for term in forbidden_terms:
                normalized_term = normalize_text(term)
                expanded_term = normalize_text(term.replace("-", " ").replace("_", " "))
                if normalized_term not in line and expanded_term not in line:
                    continue
                owner = find_term_owner(plan, term)
                owner_text = f"; `{term}` belongs to v{owner}" if owner and owner != current_rung else ""
                violations.append(
                    f"SCOPE_CONFLICT: {path.relative_to(root)}:{line_number} names v{current_rung} with `{term}` but release-plan.md says v{current_rung} is {rung_title}{owner_text}."
                )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", help="Pipeline run id under .agent-runs/.")
    args = parser.parse_args()

    if not args.run:
        print("check_release_docs_consistency: no --run argument provided; skipping (no-op outside a pipeline run).")
        return 0

    violations = evaluate_release_docs_consistency(args.run)
    if violations:
        print("check_release_docs_consistency: FAIL")
        print("  violations:")
        for item in violations:
            print(f"    - {item}")
        return 1

    print("check_release_docs_consistency: PASS - release docs match the locked canonical rung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
