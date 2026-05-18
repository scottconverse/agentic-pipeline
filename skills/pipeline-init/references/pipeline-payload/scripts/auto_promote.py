#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Machine-checkable promote decision for the agentic pipeline (v0.5).

Reads the artifacts produced by the verifier, critic, drift-detector,
policy, and judge stages. Decides whether the manager gate can be
auto-promoted (no human approval required) or whether it must remain a
human gate.

Auto-promote is eligible only when ALL of the following are true:

  1. `verifier-report.md` exists and its §0 count line shows zero
     NOT MET and zero PARTIAL criteria.
  2. `critic-report.md` exists and its §2 count line shows zero
     blocker and zero critical findings.
  3. `drift-report.md` exists and its §2 count line shows zero blocker
     drift items.
  4. `policy-report.md` exists and contains "POLICY: ALL CHECKS PASSED".
  5. If `judge-metrics.yaml` exists (i.e., the v0.4 judge layer was
     active for this run), it reports zero `judged_block` and zero
     `human_blocked` dispositions.
  6. `implementation-report.md` exists and contains a clean test output
     line ("all tests passed" / "X passed, 0 failed" / equivalent).

When all six conditions hold, this script writes a preset
`manager-decision.md` at `.agent-runs/<run-id>/manager-decision.md`
with `**Decision: PROMOTE**` and a citation block listing each of the
six conditions and the evidence that satisfied them. The runner
detects this preset and short-circuits the manager stage's human
approval gate.

When any condition fails, this script writes `auto-promote-report.md`
naming the failing conditions, exits 1, and the manager stage runs
normally with the human approval gate active.

Conservative by default: any parse error, missing file, or ambiguous
count is treated as condition failure. Auto-promote should only fire
on clean, unambiguous green.

The fix from PR #7 (resolve REPO_ROOT for both source and installed
layouts) is applied here as well.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from directive_utils import (
        DirectiveError,
        compare_preapproved,
        ensure_hash_integrity,
        evaluate_assertions,
        load_directive,
    )
except ModuleNotFoundError:  # pragma: no cover - package import in tests
    from scripts.directive_utils import (
        DirectiveError,
        compare_preapproved,
        ensure_hash_integrity,
        evaluate_assertions,
        load_directive,
    )

CRITERIA_LINE_RE = re.compile(
    r"\*\*Criteria:\s*(\d+)\s+total,\s*(\d+)\s+MET,\s*(\d+)\s+PARTIAL,\s*(\d+)\s+NOT MET,\s*(\d+)\s+NOT APPLICABLE\*\*"
)
FINDINGS_LINE_RE = re.compile(
    r"\*\*Findings:\s*(\d+)\s+total,\s*(\d+)\s+blocker,\s*(\d+)\s+critical,\s*(\d+)\s+major,\s*(\d+)\s+minor\*\*"
)
DRIFT_LINE_RE = re.compile(r"\*\*Drift:\s*(\d+)\s+total,\s*(\d+)\s+blocker\*\*")
POLICY_PASS_LINE = "POLICY: ALL CHECKS PASSED"
TEST_PASS_PATTERNS = (
    re.compile(r"\b(\d+)\s+passed(?:,\s*0\s+failed)?", re.IGNORECASE),
    re.compile(r"all tests passed", re.IGNORECASE),
    re.compile(r"\bpassed,\s*0\s+failed\b", re.IGNORECASE),
)


def _find_repo_root() -> Path:
    """Resolve the repo root. Same layout-detection as the other policy scripts."""
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == "policy" and script_dir.parent.name == "scripts":
        return script_dir.parents[1]
    return script_dir.parent


REPO_ROOT = _find_repo_root()
RUN_DIR_BASE = REPO_ROOT / ".agent-runs"


class ConditionResult:
    """Per-condition pass/fail with evidence for the decision file."""

    __slots__ = ("name", "passed", "evidence")

    def __init__(self, name: str, passed: bool, evidence: str) -> None:
        self.name = name
        self.passed = passed
        self.evidence = evidence


def _check_verifier(run_dir: Path) -> ConditionResult:
    path = run_dir / "verifier-report.md"
    if not path.exists():
        return ConditionResult("verifier-clean", False, f"{path.name} missing")
    text = path.read_text(encoding="utf-8", errors="replace")
    m = CRITERIA_LINE_RE.search(text)
    if not m:
        return ConditionResult(
            "verifier-clean",
            False,
            "verifier-report.md missing or malformed §0 criteria count line "
            "(expected `**Criteria: T total, M MET, P PARTIAL, N NOT MET, A NOT APPLICABLE**`)",
        )
    total, met, partial, not_met, na = (int(x) for x in m.groups())
    if total != met + partial + not_met + na:
        return ConditionResult(
            "verifier-clean",
            False,
            f"verifier count line inconsistent: {total} total != {met}+{partial}+{not_met}+{na}",
        )
    if not_met != 0 or partial != 0:
        return ConditionResult(
            "verifier-clean",
            False,
            f"verifier reports {not_met} NOT MET and {partial} PARTIAL criterion(a). Auto-promote requires zero of each.",
        )
    return ConditionResult(
        "verifier-clean",
        True,
        f"verifier-report.md §0: {total} total criteria, {met} MET, {na} NOT APPLICABLE, 0 PARTIAL, 0 NOT MET.",
    )


def _check_critic(run_dir: Path) -> ConditionResult:
    path = run_dir / "critic-report.md"
    if not path.exists():
        return ConditionResult("critic-clean", False, f"{path.name} missing")
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FINDINGS_LINE_RE.search(text)
    if not m:
        return ConditionResult(
            "critic-clean",
            False,
            "critic-report.md missing or malformed §2 findings count line "
            "(expected `**Findings: T total, B blocker, C critical, M major, N minor**`)",
        )
    total, blocker, critical, major, minor = (int(x) for x in m.groups())
    if total != blocker + critical + major + minor:
        return ConditionResult(
            "critic-clean",
            False,
            f"critic count line inconsistent: {total} total != {blocker}+{critical}+{major}+{minor}",
        )
    if blocker != 0 or critical != 0:
        return ConditionResult(
            "critic-clean",
            False,
            f"critic reports {blocker} blocker and {critical} critical finding(s). Auto-promote requires zero of each.",
        )
    return ConditionResult(
        "critic-clean",
        True,
        f"critic-report.md §2: {total} findings ({blocker} blocker, {critical} critical, {major} major, {minor} minor).",
    )


def _check_drift(run_dir: Path) -> ConditionResult:
    path = run_dir / "drift-report.md"
    if not path.exists():
        return ConditionResult("drift-clean", False, f"{path.name} missing")
    text = path.read_text(encoding="utf-8", errors="replace")
    m = DRIFT_LINE_RE.search(text)
    if not m:
        return ConditionResult(
            "drift-clean",
            False,
            "drift-report.md missing or malformed §2 drift count line "
            "(expected `**Drift: T total, B blocker**`)",
        )
    total, blocker = (int(x) for x in m.groups())
    if blocker != 0:
        return ConditionResult(
            "drift-clean",
            False,
            f"drift-detector reports {blocker} blocker drift item(s). Auto-promote requires zero blocker drift.",
        )
    return ConditionResult(
        "drift-clean",
        True,
        f"drift-report.md §2: {total} drift item(s), 0 blocker.",
    )


def _check_policy(run_dir: Path) -> ConditionResult:
    path = run_dir / "policy-report.md"
    if not path.exists():
        return ConditionResult("policy-passed", False, f"{path.name} missing")
    text = path.read_text(encoding="utf-8", errors="replace")
    if POLICY_PASS_LINE not in text:
        return ConditionResult(
            "policy-passed",
            False,
            f"policy-report.md does not contain `{POLICY_PASS_LINE}`. Policy gate did not pass.",
        )
    return ConditionResult(
        "policy-passed", True, f"policy-report.md: `{POLICY_PASS_LINE}` present."
    )


def _check_judge(run_dir: Path) -> ConditionResult:
    """If the judge layer was active for this run, require zero blocks.

    judge-metrics.yaml is only present when .pipelines/action-classification.yaml
    was present at run start. When absent, the run did not use the judge layer
    and this condition passes vacuously.
    """
    path = run_dir / "judge-metrics.yaml"
    if not path.exists():
        return ConditionResult(
            "judge-clean", True, "judge layer was not active for this run (no judge-metrics.yaml)."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    judged_block = _extract_int(text, "judged_block")
    human_blocked = _extract_int(text, "human_blocked")
    if judged_block is None or human_blocked is None:
        return ConditionResult(
            "judge-clean",
            False,
            "judge-metrics.yaml missing `judged_block` or `human_blocked` counter under `by_disposition`.",
        )
    if judged_block != 0 or human_blocked != 0:
        return ConditionResult(
            "judge-clean",
            False,
            f"judge layer reports {judged_block} judged_block and {human_blocked} human_blocked. "
            "Auto-promote requires zero of each.",
        )
    return ConditionResult(
        "judge-clean",
        True,
        f"judge-metrics.yaml: judged_block=0, human_blocked=0.",
    )


_TEST_DIR_PREFIXES = ("tests/", "test/", "tests", "test")


def _manifest_forbids_tests(run_dir: Path) -> tuple[bool, list[str]]:
    """True if the manifest's `forbidden_paths` covers a test directory.

    Used by `_check_tests` to recognize docs-only and tests-out-of-scope
    runs, where condition 6 should pass vacuously rather than block on
    the absence of a test-pass signal it could not possibly have.

    Returns (forbids_tests, matching_entries). Empty/missing manifest →
    (False, []) — strict default, behavior unchanged from v1.3.0.
    """
    manifest_path = run_dir / "manifest.yaml"
    if not manifest_path.exists():
        return False, []
    text = manifest_path.read_text(encoding="utf-8", errors="replace")
    in_forbidden = False
    matches: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if "#" in line:
            hash_idx = line.find("#")
            if hash_idx == 0 or line[hash_idx - 1].isspace():
                line = line[:hash_idx].rstrip()
        if not line:
            continue
        stripped = line.strip()
        if stripped.startswith("forbidden_paths:"):
            value_part = stripped[len("forbidden_paths:"):].strip()
            # Flow style: forbidden_paths: ["tests/", "other/"]  or  forbidden_paths: []
            if value_part.startswith("["):
                in_forbidden = False
                inner = value_part.strip("[]").strip()
                if inner:
                    for item in inner.split(","):
                        entry = item.strip().strip("\"'")
                        if not entry:
                            continue
                        _record_if_test_dir(entry, matches)
                continue
            # Block style: forbidden_paths: followed by - entries
            in_forbidden = True
            continue
        if not raw.startswith((" ", "\t")) and stripped.endswith(":"):
            in_forbidden = False
            continue
        if in_forbidden and stripped.startswith("- "):
            value = stripped[2:].strip().strip("\"'")
            _record_if_test_dir(value, matches)
        elif in_forbidden and not stripped.startswith("- "):
            in_forbidden = False
    return bool(matches), matches


def _record_if_test_dir(value: str, matches: list[str]) -> None:
    """If `value` resolves to a test directory, append the raw form to matches."""
    normalized = value.lstrip("/")
    for prefix in _TEST_DIR_PREFIXES:
        prefix_with_slash = prefix.rstrip("/") + "/"
        if normalized == prefix or normalized == prefix.rstrip("/") or normalized.startswith(prefix_with_slash):
            matches.append(value)
            return


def _check_tests(run_dir: Path) -> ConditionResult:
    """Look in implementation-report.md for a clean test output signal.

    Conservative: the report must contain a recognizable "tests passed"
    pattern AND no occurrence of `failed=[1-9]` style failure tokens.

    Vacuous-pass exception (v1.3.1): when the manifest's
    `forbidden_paths` explicitly forbids the test directory (so the
    run was barred from running or modifying tests), this condition
    passes with an explanation when `implementation-report.md` is
    absent. Mirrors the `_check_judge` vacuous-pass behavior and
    closes the docs-only false-stop that surfaced in the v1.2.1
    PROMOTED report.
    """
    path = run_dir / "implementation-report.md"
    if not path.exists():
        forbids, matches = _manifest_forbids_tests(run_dir)
        if forbids:
            return ConditionResult(
                "tests-passed",
                True,
                "implementation-report.md absent; manifest forbids test paths "
                f"({', '.join(matches)}) — tests were out of scope for this run, "
                "so condition 6 passes without a test-pass signal.",
            )
        return ConditionResult(
            "tests-passed",
            False,
            f"{path.name} missing (if tests are out of scope for this run, "
            "add the test directory to manifest.forbidden_paths)",
        )
    text = path.read_text(encoding="utf-8", errors="replace")

    if re.search(r"\b\d+\s+failed\b", text):
        # But allow "0 failed" specifically.
        if not re.search(r"\b0\s+failed\b", text):
            return ConditionResult(
                "tests-passed", False, "implementation-report.md contains a non-zero failure count."
            )

    for pattern in TEST_PASS_PATTERNS:
        if pattern.search(text):
            return ConditionResult(
                "tests-passed",
                True,
                f"implementation-report.md contains a clean test-pass signal matching `{pattern.pattern}`.",
            )

    return ConditionResult(
        "tests-passed",
        False,
        "implementation-report.md does not contain a recognizable test-pass signal "
        "(expected `N passed[, 0 failed]` or `all tests passed`).",
    )


def _extract_int(text: str, key: str) -> int | None:
    """Find a `key: <int>` line in a flat YAML-ish blob. Returns None if absent or malformed."""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Directive-contract integration (v2.0)
#
# When `.agent-runs/<run-id>/directive.yaml` exists and is bound, auto-promote
# also re-verifies that manifest.yaml and scope-lock.yaml still match the
# directive's preapproved content (downstream defense-in-depth — the binding
# can't be the only proof of conformance), then evaluates every directive
# `acceptance.manager` assertion on top of the six base conditions.
#
# Two module-level callable helpers are exposed so directives can reference
# them by public name: `no_unresolved_open_caveats` and
# `verifier_covers_manifest_expected_outputs`. The directive YAML names them
# verbatim under `acceptance.manager[].name`.
# ---------------------------------------------------------------------------


def no_unresolved_open_caveats(ctx, args):
    checked: list[str] = []
    for path in ctx.run_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^##\s+Open Caveats / Release Risks\s*$", text, re.MULTILINE)
        if not match:
            continue
        checked.append(path.name)
        section = text[match.end() :]
        next_heading = re.search(r"^##\s+", section, re.MULTILINE)
        body = section[: next_heading.start()] if next_heading else section
        unresolved = [
            line.strip()
            for line in body.splitlines()
            if line.strip().startswith("-") and "INTENTIONAL DEFERRAL:" not in line
        ]
        if unresolved:
            return False, f"{path.name} has unresolved caveat(s): {'; '.join(unresolved)}"
    return True, "no unresolved Open Caveats / Release Risks bullets" + (f" in {', '.join(checked)}" if checked else "")


def verifier_covers_manifest_expected_outputs(ctx, args):
    import yaml

    manifest = yaml.safe_load((ctx.run_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
    root = manifest.get("pipeline_run") if isinstance(manifest.get("pipeline_run"), dict) else manifest
    outputs = root.get("expected_outputs") or []
    report = (ctx.run_dir / "verifier-report.md").read_text(encoding="utf-8", errors="replace").lower()
    missing = [str(item) for item in outputs if str(item).lower() not in report]
    if missing:
        return False, "verifier-report.md does not cite expected output(s): " + ", ".join(missing)
    return True, f"verifier-report.md cites {len(outputs)} manifest expected output(s)"


def _check_directive_manager(run_id: str, run_dir: Path) -> list[ConditionResult]:
    try:
        ctx = load_directive(REPO_ROOT, run_id)
        if ctx is None:
            return []
        ensure_hash_integrity(ctx)
        conformance_results: list[ConditionResult] = []
        for name, artifact, key in (
            ("directive-manifest-conformance", "manifest.yaml", "manifest"),
            ("directive-scope-lock-conformance", "scope-lock.yaml", "scope_lock"),
        ):
            matched, diff = compare_preapproved(ctx, artifact, key)
            if not matched:
                conformance_results.append(
                    ConditionResult(
                        name,
                        False,
                        f"{artifact} diverges from directive {ctx.current_hash}: {diff.strip()}",
                    )
                )
        if conformance_results:
            return conformance_results
        acceptance = ctx.directive.get("acceptance") or {}
        assertions = acceptance.get("manager") or []
        if not isinstance(assertions, list):
            raise DirectiveError("directive acceptance.manager must be a list")
        artifact_texts = {
            path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in run_dir.glob("*")
            if path.is_file()
        }
        results = evaluate_assertions(
            ctx=ctx,
            assertions=assertions,
            artifact_texts=artifact_texts,
            callable_namespace=__name__,
        )
        return [
            ConditionResult(
                f"directive-manager:{result.id}",
                result.passed,
                f"directive {ctx.current_hash} ({ctx.author}, {ctx.authority}): {result.evidence}",
            )
            for result in results
        ]
    except DirectiveError as exc:
        return [ConditionResult("directive-manager:integrity", False, str(exc))]


def _directive_summary(run_id: str) -> str:
    try:
        ctx = load_directive(REPO_ROOT, run_id)
        if ctx is None:
            return "No directive contract was present for this run."
        return f"Directive hash `{ctx.current_hash}`; author `{ctx.author}`; authority `{ctx.authority}`."
    except DirectiveError as exc:
        return f"Directive contract unavailable for citation: {exc}"


def _write_decision(run_id: str, run_dir: Path, conditions: list[ConditionResult]) -> None:
    """Write the preset manager-decision.md that the runner uses to short-circuit."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "**Decision: PROMOTE**",
        "",
        "_Generated by `scripts/auto_promote.py` — all auto-promote conditions satisfied "
        "(six base + N directive). "
        f"Timestamp: {timestamp}._",
        "",
        "## Directive contract",
        "",
        _directive_summary(run_id),
        "",
        "## Citation",
        "",
        "Every condition required for auto-promote was satisfied. Evidence:",
        "",
    ]
    for c in conditions:
        marker = "PASS" if c.passed else "FAIL"
        lines.append(f"- **{marker}** `{c.name}` — {c.evidence}")
    lines.extend(
        [
            "",
            "## Disposition",
            "",
            "PROMOTE — proceed to merge per the manifest's `required_gates`. The final "
            "`human_approval_merge` gate is outside this pipeline; merge via PR review.",
            "",
            "## Audit-pattern dispatch",
            "",
            "Any non-blocker findings from the critic and any non-blocker drift items "
            "have already been recorded in their respective reports. Per the project's "
            "overflow rule, those items go to `next-cleanup.md` or the next rung's P1 list "
            "as named there.",
            "",
        ]
    )
    (run_dir / "manager-decision.md").write_text("\n".join(lines), encoding="utf-8")


def _write_report(run_dir: Path, conditions: list[ConditionResult]) -> None:
    """Write auto-promote-report.md naming which conditions failed."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# auto-promote — NOT_ELIGIBLE",
        "",
        f"_Generated by `scripts/auto_promote.py` at {timestamp}._",
        "",
        "## Conditions",
        "",
    ]
    for c in conditions:
        marker = "PASS" if c.passed else "FAIL"
        lines.append(f"- **{marker}** `{c.name}` — {c.evidence}")
    lines.extend(
        [
            "",
            "## What happens next",
            "",
            "The manager stage runs normally with the human-approval gate active. "
            "Resolve the failing conditions (fix the work, re-run the failing stages) "
            "and re-invoke the pipeline to retry auto-promote.",
            "",
        ]
    )
    (run_dir / "auto-promote-report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument(
        "--run",
        required=True,
        help="Pipeline run id (directory under .agent-runs/).",
    )
    args = parser.parse_args()

    run_dir = RUN_DIR_BASE / args.run
    if not run_dir.is_dir():
        print(f"auto_promote: FAIL — run directory not found at {run_dir}", file=sys.stderr)
        return 2

    conditions = [
        _check_verifier(run_dir),
        _check_critic(run_dir),
        _check_drift(run_dir),
        _check_policy(run_dir),
        _check_judge(run_dir),
        _check_tests(run_dir),
    ]
    conditions.extend(_check_directive_manager(args.run, run_dir))

    all_passed = all(c.passed for c in conditions)

    # Print a compact summary regardless.
    print("auto_promote: conditions")
    for c in conditions:
        marker = "PASS" if c.passed else "FAIL"
        print(f"  [{marker}] {c.name} — {c.evidence}")

    if all_passed:
        _write_decision(args.run, run_dir, conditions)
        print("auto_promote: ELIGIBLE — manager-decision.md written with PROMOTE.")
        return 0

    _write_report(run_dir, conditions)
    print("auto_promote: NOT_ELIGIBLE — see auto-promote-report.md; manager stage will run with human gate.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
