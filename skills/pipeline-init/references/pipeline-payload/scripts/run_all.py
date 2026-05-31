#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run every policy check and produce a combined PROMOTE/BLOCK report.

Wired into ``.pipelines/feature.yaml`` and ``.pipelines/bugfix.yaml`` as
the ``policy`` stage. The manager role uses this report to decide
PROMOTE / BLOCK / REPLAN.

Exit code: 0 only if every check passes. 1 if any check fails. The final
report line is one of:
  POLICY: ALL CHECKS PASSED
  POLICY: <N> CHECK(S) FAILED

When ``--run`` is given, the same content is also written directly to
``.agent-runs/<run-id>/policy-report.md`` so the marker line is
guaranteed to appear in the artifact regardless of how the orchestrator
captures stdout (v1.3.1 — removes the false-stop where auto-promote
fails condition 4 because the orchestrator's stdout-to-file capture
lost the marker, even though the policy gate actually passed).

To add project-specific policy checks, drop them in this directory next
to the generic ones and add them to the CHECKS list below.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = find_repo_root(__file__)
RUN_DIR_BASE = REPO_ROOT / ".agent-runs"

# Order matters only for human readability of the combined report.
# Add project-specific checks here (e.g., a custom check_module_boundaries.py).
#
# v2.0 wires the directive / scope-lock / control-loop / readiness /
# decision-ledger checks into the policy stage so they actually fire
# during a normal `/agent-pipeline-claude:run` (audit ENG-003). Each
# v2.0 check carries a prerequisite-artifact entry in
# CHECK_PREREQUISITES — when the artifact is absent from the run dir,
# the check is reported SKIP (PASS) rather than FAIL. This lets v1.x
# runs that don't opt into v2.0 enforcement pass through cleanly while
# v2.0-enabled runs get the full gate.
CHECKS: list[tuple[str, list[str]]] = [
    ("check_manifest_schema", ["check_manifest_schema.py"]),
    # v1.2.0: cross-stage integrity — manifest SHA must match the pin
    # taken at preflight. Catches mid-run manifest mutation.
    ("check_manifest_immutable", ["check_manifest_immutable.py", "--check"]),
    ("check_allowed_paths", ["check_allowed_paths.py"]),
    ("check_no_todos", ["check_no_todos.py"]),
    ("check_adr_gate", ["check_adr_gate.py"]),
    # v1.2.0: STAGE_DONE markers required through `execute` by policy stage.
    ("check_stage_done", ["check_stage_done.py", "--through", "execute"]),
    # v2.0 enforcement layer — conditional on the run opting in
    # (prerequisite artifact must exist; see CHECK_PREREQUISITES).
    ("check_directive_conformance", ["check_directive_conformance.py"]),
    ("check_scope_lock", ["check_scope_lock.py"]),
    ("check_rung_file_ownership", ["check_rung_file_ownership.py"]),
    ("check_release_docs_consistency", ["check_release_docs_consistency.py"]),
    ("check_pipeline_control_loop", ["check_pipeline_control_loop.py"]),
    ("check_execute_readiness", ["check_execute_readiness.py"]),
    ("check_decision_ledger", ["check_decision_ledger.py"]),
]

# Maps check name -> relative path under <RUN_DIR_BASE>/<run-id>/ that
# must exist for the check to be invoked. When the prerequisite is
# absent the check is SKIPPED (counted as PASS for the policy gate),
# never invoked. The conditional-skip pattern lets v2.0 enforcement
# stay opt-in: a project that hasn't authored a scope-lock.yaml yet
# isn't blocked from passing policy on every run.
CHECK_PREREQUISITES: dict[str, str] = {
    "check_directive_conformance": "directive.yaml",
    "check_scope_lock": "scope-lock.yaml",
    "check_rung_file_ownership": "scope-lock.yaml",
    "check_release_docs_consistency": "scope-lock.yaml",
    "check_pipeline_control_loop": "active-control-state.md",
    "check_execute_readiness": "implementation-report.md",
    "check_decision_ledger": "decision-ledger.ndjson",
}

# Maps a v2.0/v3.0 gate name (as written in the manifest's `required_gates`)
# to the policy checks that enforce it. When a run's manifest *declares* one
# of these gates, the matching check's prerequisite artifact is mandatory: a
# missing prerequisite is a FAIL, not a silent SKIP (audit ENG-008 — a run
# that claims a gate level must not pass policy with that gate un-run).
# Gates whose checks have a prerequisite but no documented `required_gates`
# name (control-loop, decision-ledger) intentionally stay skip-when-absent.
GATE_ENFORCING_CHECKS: dict[str, set[str]] = {
    "directive_bound": {"check_directive_conformance"},
    "scope_lock_authority": {
        "check_scope_lock",
        "check_rung_file_ownership",
        "check_release_docs_consistency",
    },
    "execute_readiness": {"check_execute_readiness"},
}


def _declared_gates(run_id: str | None) -> set[str]:
    """Return the gate names the run's manifest declares in `required_gates`.

    Empty when there is no run, no manifest, or the manifest can't be parsed
    — in those cases the gate-level FAIL path below is inert and the prior
    skip-when-absent behavior holds. (A malformed manifest is independently
    caught and failed by `check_manifest_schema`, so swallowing the parse
    error here does not let a broken manifest through the overall gate.)
    """
    if not run_id:
        return set()
    manifest_path = RUN_DIR_BASE / run_id / "manifest.yaml"
    if not manifest_path.exists():
        return set()
    try:
        import yaml

        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    root = data.get("pipeline_run") if isinstance(data.get("pipeline_run"), dict) else data
    if not isinstance(root, dict):
        return set()
    gates = root.get("required_gates") or []
    if not isinstance(gates, list):
        return set()
    return {str(g).strip() for g in gates if isinstance(g, str)}


def _run(check_name: str, script_args: list[str], extra_args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, str(THIS_DIR / script_args[0]), *script_args[1:], *extra_args]
    # Decode child output as UTF-8 (audit QA-006): the checks print decorative
    # Unicode, and a locale-default (cp1252) decode mangles it in the combined
    # report. errors="replace" keeps a noisy byte from ever crashing the gate.
    proc = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace", check=False
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.rstrip()


def _prerequisite_present(check_name: str, run_id: str | None) -> tuple[bool, str]:
    """Return (present, message).

    When ``check_name`` is in ``CHECK_PREREQUISITES``:
      * If no run id, the prerequisite cannot be checked → report
        absent (skip with reason).
      * If the prerequisite file is missing from the run dir → absent.
    When ``check_name`` has no prerequisite entry → present (always run).
    """
    prereq = CHECK_PREREQUISITES.get(check_name)
    if prereq is None:
        return True, ""
    if not run_id:
        return False, f"no --run argument (cannot check for {prereq})"
    candidate = RUN_DIR_BASE / run_id / prereq
    if candidate.exists():
        return True, ""
    return False, f"{prereq} not present in run dir"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", action="version", version="agent-pipeline-claude 3.0.1"
    )
    parser.add_argument(
        "--run",
        help="Pipeline run id, passed through to checks that consume the manifest.",
    )
    args = parser.parse_args()

    extra_for_run_consumers = ["--run", args.run] if args.run else []
    # Checks that consume the run id (read manifest at .agent-runs/<run>/manifest.yaml).
    run_consumers = {
        "check_allowed_paths",
        "check_manifest_schema",
        "check_manifest_immutable",
        "check_stage_done",
        # v2.0 checks all consume --run; conditional-skip prevents
        # spurious failures when the prereq artifact is absent.
        "check_directive_conformance",
        "check_scope_lock",
        "check_rung_file_ownership",
        "check_release_docs_consistency",
        "check_pipeline_control_loop",
        "check_execute_readiness",
        "check_decision_ledger",
    }

    # A check is *mandated* when the manifest declares the gate it enforces.
    # For mandated checks a missing prerequisite is a FAIL, not a SKIP.
    declared = _declared_gates(args.run)
    mandated_checks: set[str] = set()
    mandating_gate: dict[str, str] = {}
    for gate, checks in GATE_ENFORCING_CHECKS.items():
        if gate in declared:
            for check in checks:
                mandated_checks.add(check)
                mandating_gate[check] = gate

    # status is one of "PASS" / "FAIL" / "SKIP". SKIP never affects the exit
    # code (back-compat for v1.x runs that don't opt into a gate) but is
    # surfaced distinctly so the operator sees the real enforcement depth.
    results: list[tuple[str, str, str]] = []
    for name, script_args in CHECKS:
        present, skip_reason = _prerequisite_present(name, args.run)
        if not present:
            if name in mandated_checks:
                gate = mandating_gate[name]
                results.append((
                    name,
                    "FAIL",
                    f"manifest declares gate `{gate}` but its prerequisite is absent: {skip_reason}",
                ))
            else:
                results.append((name, "SKIP", skip_reason))
            continue
        extra = extra_for_run_consumers if name in run_consumers else []
        passed, output = _run(name, script_args, extra)
        results.append((name, "PASS" if passed else "FAIL", output))

    failed = [name for name, status, _ in results if status == "FAIL"]
    skipped = [name for name, status, _ in results if status == "SKIP"]

    report_lines: list[str] = []
    report_lines.append("=" * 64)
    report_lines.append("Policy checks")
    report_lines.append("=" * 64)
    for name, status, output in results:
        report_lines.append(f"\n[{status}] {name}")
        if output:
            for line in output.splitlines():
                report_lines.append(f"  {line}")
    report_lines.append("")
    report_lines.append("-" * 64)
    if failed:
        report_lines.append(f"POLICY: {len(failed)} CHECK(S) FAILED")
        for name in failed:
            report_lines.append(f"  - {name}")
    else:
        # Keep the exact "POLICY: ALL CHECKS PASSED" token as a prefix so
        # substring consumers (auto_promote condition 4) still match, while
        # making skipped (un-run) gates visible at a glance — ENG-008.
        suffix = f" ({len(skipped)} skipped — prerequisites absent)" if skipped else ""
        report_lines.append(f"POLICY: ALL CHECKS PASSED{suffix}")
    if skipped:
        report_lines.append("  skipped (prerequisite artifact absent; gate not declared in manifest):")
        for name in skipped:
            report_lines.append(f"    - {name}")

    report_text = "\n".join(report_lines) + "\n"
    print(report_text, end="")

    # v1.3.1: when invoked inside a real pipeline run, write the
    # canonical artifact directly. Removes dependence on the
    # orchestrator's stdout-to-file capture and guarantees the POLICY
    # marker line is present for auto_promote to find.
    if args.run:
        report_path = RUN_DIR_BASE / args.run / "policy-report.md"
        if report_path.parent.is_dir():
            report_path.write_text(report_text, encoding="utf-8")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
