#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: product work must match the canonical release-plan rung.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_scope_lock.py).
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


def evaluate_scope_lock(run_id: str, run_dir: Path = RUN_DIR, root: Path = REPO_ROOT) -> list[str]:
    violations: list[str] = []
    try:
        lock_path, lock = load_scope_lock(run_dir, run_id)
    except FileNotFoundError as exc:
        return [
            f"scope-lock.yaml missing at {exc.args[0]}. Create it before product work starts."
        ]

    current_rung = scalar_value(lock, "current_rung")
    canonical_source = scalar_value(lock, "canonical_source")
    rung_title = scalar_value(lock, "rung_title")
    proves = scalar_value(lock, "proves")

    for key, value in (
        ("current_rung", current_rung),
        ("canonical_source", canonical_source),
        ("rung_title", rung_title),
        ("proves", proves),
    ):
        if not value:
            violations.append(f"`{key}` is required in {lock_path}.")

    if violations:
        return violations

    plan_path = (root / canonical_source).resolve()
    if not plan_path.exists():
        return [f"canonical_source not found: {canonical_source}"]

    # v2.1.0 project-shape adapter: multi-repo-admin and library shapes
    # don't use rung-versioned release plans. The canonical_source for
    # these is SPEC.md (or another non-rung doc). When the shape is one
    # of those, accept any non-empty rung title present anywhere in the
    # canonical doc. If the rung name isn't even mentioned, that's
    # still a SCOPE_CONFLICT (the scope-lock should at least reference
    # the canonical authority by name).
    try:
        from policy_utils import read_project_shape
    except ModuleNotFoundError:  # pragma: no cover - installed layout
        from scripts.policy_utils import read_project_shape
    shape = read_project_shape(root)
    if shape in ("multi-repo-admin", "library"):
        plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
        plan_normalized = normalize_text(plan_text)
        if normalize_text(current_rung) not in plan_normalized:
            return [
                f"scope-lock current_rung `{current_rung}` is not mentioned anywhere in {canonical_source}; "
                "either add a section/heading referencing it or update scope-lock to match the canonical doc."
            ]
        if normalize_text(proves) and normalize_text(proves) not in plan_normalized:
            violations.append(
                f"SCOPE_CONFLICT: scope-lock `proves` text not found in {canonical_source} "
                f"(project_shape={shape}; full-doc match used)."
            )
        for module in list_value(lock, "required_modules"):
            if normalize_text(module) not in plan_normalized:
                violations.append(
                    f"SCOPE_CONFLICT: required module `{module}` is not mentioned in {canonical_source}."
                )
        return violations

    plan = parse_release_plan(plan_path)
    section = plan.get(current_rung)
    if section is None:
        return [f"release plan has no rung `{current_rung}` in {canonical_source}"]

    if normalize_text(section.title) != normalize_text(rung_title):
        violations.append(
            f"SCOPE_CONFLICT: release-plan.md says v{current_rung} is {section.title}; scope-lock says {rung_title}. Replan or amend the canonical source before editing."
        )

    if normalize_text(proves) not in normalize_text(section.body):
        violations.append(
            f"SCOPE_CONFLICT: scope-lock `proves` text does not match v{current_rung} in {canonical_source}."
        )

    for module in list_value(lock, "required_modules"):
        if normalize_text(module) not in normalize_text(section.body):
            violations.append(
                f"SCOPE_CONFLICT: required module `{module}` is not present in v{current_rung} canonical plan text."
            )

    for bullet in list_value(lock, "scope_bullets") + list_value(lock, "exit_criteria"):
        if normalize_text(bullet) not in normalize_text(section.body):
            violations.append(
                f"SCOPE_CONFLICT: required scope/exit criterion `{bullet}` is not present in v{current_rung} canonical plan text."
            )

    for term in list_value(lock, "forbidden_feature_terms_without_replan"):
        owner = find_term_owner(plan, term)
        if owner and owner != current_rung:
            continue
        if owner == current_rung:
            violations.append(
                f"SCOPE_CONFLICT: forbidden term `{term}` appears inside v{current_rung}; remove it from the lock or amend the release plan explicitly."
            )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 3.0.1")
    parser.add_argument("--run", help="Pipeline run id under .agent-runs/.")
    args = parser.parse_args()

    if not args.run:
        print("check_scope_lock: no --run argument provided; skipping (no-op outside a pipeline run).")
        return 0

    violations = evaluate_scope_lock(args.run)
    if violations:
        print("check_scope_lock: FAIL")
        print("  violations:")
        for item in violations:
            print(f"    - {item}")
        return 1

    _, lock = load_scope_lock(RUN_DIR, args.run)
    print(
        "check_scope_lock: PASS - "
        f"canonical_rung: {scalar_value(lock, 'current_rung')} {scalar_value(lock, 'rung_title')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
