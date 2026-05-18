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
    # v1.2.1: autonomous-mode compliance — verifies the LLM honored the
    # autonomous grant correctly (no chat-wait messages slipping through,
    # no forbidden actions in run.log). Silent skip for HUMAN-MODE runs.
    ("check_autonomous_compliance", ["check_autonomous_compliance.py"]),
]


def _run(check_name: str, script_args: list[str], extra_args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, str(THIS_DIR / script_args[0]), *script_args[1:], *extra_args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", action="version", version="agent-pipeline-claude 1.3.1"
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
        "check_autonomous_compliance",
    }

    results: list[tuple[str, bool, str]] = []
    for name, script_args in CHECKS:
        extra = extra_for_run_consumers if name in run_consumers else []
        passed, output = _run(name, script_args, extra)
        results.append((name, passed, output))

    failed = [name for name, passed, _ in results if not passed]

    report_lines: list[str] = []
    report_lines.append("=" * 64)
    report_lines.append("Policy checks")
    report_lines.append("=" * 64)
    for name, passed, output in results:
        status = "PASS" if passed else "FAIL"
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
        report_lines.append("POLICY: ALL CHECKS PASSED")

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
