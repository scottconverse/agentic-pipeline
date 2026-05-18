#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evaluate plan.md against directive-declared plan acceptance assertions.

Ported from agent-pipeline-codex v0.9.0 (post-PR-#5 amendments):
- Downstream re-verify: before evaluating plan assertions, re-checks that
  manifest.yaml and scope-lock.yaml still match the directive's preapproved
  content. A divergence returns exit 2 so the orchestrator stops the run
  rather than silently auto-approving against a tampered contract.
"""

from __future__ import annotations

import argparse
import sys

try:
    from directive_utils import (
        DirectiveError,
        compare_preapproved,
        ensure_hash_integrity,
        evaluate_assertions,
        load_directive,
    )
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover
    from scripts.directive_utils import (
        DirectiveError,
        compare_preapproved,
        ensure_hash_integrity,
        evaluate_assertions,
        load_directive,
    )
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)


def mentions_failing_tests_before_execute(ctx, args):
    plan = (ctx.run_dir / "plan.md").read_text(encoding="utf-8", errors="replace")
    test_index = plan.lower().find("failing-tests-report.md")
    execute_index = plan.lower().find("implementation-report.md")
    if test_index == -1:
        return False, "plan.md does not mention failing-tests-report.md"
    if execute_index != -1 and test_index > execute_index:
        return False, "plan.md mentions implementation before failing tests"
    return True, "plan.md names failing-tests-report.md before implementation-report.md"


def mentions_manifest_expected_outputs(ctx, args):
    import yaml

    manifest = yaml.safe_load((ctx.run_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
    root = manifest.get("pipeline_run") if isinstance(manifest.get("pipeline_run"), dict) else manifest
    outputs = root.get("expected_outputs") or []
    plan = (ctx.run_dir / "plan.md").read_text(encoding="utf-8", errors="replace").lower()
    missing = [str(item) for item in outputs if str(item).lower() not in plan]
    if missing:
        return False, "plan.md does not mention expected output(s): " + ", ".join(missing)
    return True, f"plan.md mentions {len(outputs)} manifest expected output(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", required=True)
    args = parser.parse_args()

    try:
        ctx = load_directive(REPO_ROOT, args.run)
        if ctx is None:
            print("plan_directive: NO_DIRECTIVE - use existing interactive plan gate.")
            return 1
        ensure_hash_integrity(ctx)
        conformance_failures: list[str] = []
        for label, artifact, key in (
            ("directive-manifest-conformance", "manifest.yaml", "manifest"),
            ("directive-scope-lock-conformance", "scope-lock.yaml", "scope_lock"),
        ):
            matched, diff = compare_preapproved(ctx, artifact, key)
            if not matched:
                conformance_failures.append(f"[FAIL] {label} - {artifact} diverges from directive\n{diff}")
        if conformance_failures:
            print(
                "plan_directive: CONTRACT_DIVERGED - directive hash is intact but "
                "preapproved manifest/scope-lock conformance failed."
            )
            print("".join(conformance_failures))
            return 2
        plan_path = ctx.run_dir / "plan.md"
        if not plan_path.exists():
            raise DirectiveError("plan.md missing")
        acceptance = ctx.directive.get("acceptance") or {}
        assertions = acceptance.get("plan") or []
        if not isinstance(assertions, list) or not assertions:
            raise DirectiveError("directive acceptance.plan must be a non-empty list")
        results = evaluate_assertions(
            ctx=ctx,
            assertions=assertions,
            artifact_texts={"plan.md": plan_path.read_text(encoding="utf-8", errors="replace")},
            callable_namespace=__name__,
        )
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"[{marker}] {result.id} - {result.evidence}")
        if failed:
            print(f"plan_directive: FALLBACK - {len(failed)}/{len(results)} criteria failed; interactive plan gate required.")
            return 1
        print(
            "plan_directive: AUTO_APPROVE "
            f"hash={ctx.current_hash} author={ctx.author} authority={ctx.authority} criteria={len(passed)}/{len(results)}"
        )
        return 0
    except DirectiveError as exc:
        print(f"plan_directive: FALLBACK - {exc}")
        if "hash changed" in str(exc):
            return 2
        return 1


if __name__ == "__main__":
    sys.exit(main())
