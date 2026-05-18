#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: changed files and commit subjects must belong to the locked rung.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_rung_file_ownership.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scope_lock_utils import (
        changed_paths,
        find_term_owner,
        head_commit_subject,
        list_value,
        load_scope_lock,
        normalize_text,
        parse_release_plan,
        repo_root,
        scalar_value,
    )
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.scope_lock_utils import (
        changed_paths,
        find_term_owner,
        head_commit_subject,
        list_value,
        load_scope_lock,
        normalize_text,
        parse_release_plan,
        repo_root,
        scalar_value,
    )


REPO_ROOT = repo_root()
RUN_DIR = REPO_ROOT / ".agent-runs"


def _path_text(path: str) -> str:
    return normalize_text(path.replace("/", " ").replace("\\", " ").replace("-", " ").replace("_", " "))


def evaluate_rung_file_ownership(
    run_id: str,
    run_dir: Path = RUN_DIR,
    root: Path = REPO_ROOT,
    commit_message: str | None = None,
    paths: list[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    try:
        _, lock = load_scope_lock(run_dir, run_id)
    except FileNotFoundError as exc:
        return [f"scope-lock.yaml missing at {exc.args[0]}."]

    current_rung = scalar_value(lock, "current_rung")
    plan_path = root / scalar_value(lock, "canonical_source")
    plan = parse_release_plan(plan_path) if plan_path.exists() else {}
    forbidden_terms = list_value(lock, "forbidden_feature_terms_without_replan")
    allowed_terms = [normalize_text(term) for term in list_value(lock, "allowed_feature_terms")]

    check_paths = paths if paths is not None else changed_paths(root)
    subject = commit_message if commit_message is not None else head_commit_subject(root)
    surfaces = [(f"path `{path}`", _path_text(path)) for path in check_paths]
    if subject:
        surfaces.append(("commit message", normalize_text(subject)))

    for label, surface in surfaces:
        for term in forbidden_terms:
            normalized_term = normalize_text(term)
            expanded_term = normalize_text(term.replace("-", " ").replace("_", " "))
            if normalized_term not in surface and expanded_term not in surface:
                continue
            owner = find_term_owner(plan, term)
            owner_text = f" belongs to v{owner}" if owner and owner != current_rung else ""
            violations.append(
                f"SCOPE_CONFLICT: {label} contains `{term}`; release-plan.md says v{current_rung} is {scalar_value(lock, 'rung_title')}; `{term}`{owner_text}. Replan or get explicit scope amendment before editing."
            )

    for label, surface in surfaces:
        if label == "commit message":
            continue
        if any(term and term in surface for term in allowed_terms):
            continue
        # Path checks are term-based guardrails, not mandatory labels for every
        # support file. They block future-rung ownership, not neutral test/docs
        # files that are already bounded by allowed_paths.

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", help="Pipeline run id under .agent-runs/.")
    parser.add_argument("--commit-message", help="Commit subject to classify before commit-msg hooks.")
    args = parser.parse_args()

    if not args.run:
        print("check_rung_file_ownership: no --run argument provided; skipping (no-op outside a pipeline run).")
        return 0

    violations = evaluate_rung_file_ownership(args.run, commit_message=args.commit_message)
    if violations:
        print("check_rung_file_ownership: FAIL")
        print("  violations:")
        for item in violations:
            print(f"    - {item}")
        return 1

    print("check_rung_file_ownership: PASS - edited paths and commit subject match the locked rung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
