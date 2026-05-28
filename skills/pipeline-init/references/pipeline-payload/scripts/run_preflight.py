#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run preflight checks before research/plan stages execute.

v1.2.0 hardening: priority-drift + manifest-integrity gates run AFTER
the human APPROVE on the manifest and BEFORE researcher spends a model
call. Failures here cost ~$0; failures discovered later cost real money
and waste run time.

Wired into ``.pipelines/feature.yaml``, ``.pipelines/bugfix.yaml``,
and ``.pipelines/module-release.yaml`` as the ``preflight`` stage.

Preflight runs these checks in sequence:

  1. check_manifest_schema  — manifest has all required v1.2.0 fields
                              well-formed, no forbidden words.
  2. check_active_target    — manifest's advances_target aligns with
                              project's control-plane active target,
                              OR override_active_target is sufficient.
  3. check_manifest_paths   — every cited path resolves in the
                              filesystem; authorizing_source file:line
                              is valid.
  4. check_manifest_immutable --pin  — capture the SHA-256 of manifest.yaml
                                       for later cross-stage integrity check.

If ANY of (1)–(3) fails, the preflight stage fails and the run halts.
The pin in (4) is informational unless a later policy stage triggers
the --check mode.

Exit code: 0 only if every check passes. 1 if any check fails. The
final report line is one of:

  PREFLIGHT: ALL CHECKS PASSED
  PREFLIGHT: <N> CHECK(S) FAILED
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent


CHECKS: list[tuple[str, list[str], list[str]]] = [
    ("check_manifest_schema", ["check_manifest_schema.py"], ["--run"]),
    ("check_active_target", ["check_active_target.py"], ["--run"]),
    ("check_manifest_paths", ["check_manifest_paths.py"], ["--run"]),
    # Pin the manifest SHA-256 at preflight time. The --check counterpart
    # runs in run_all.py after execute, catching mid-run mutation.
    ("check_manifest_immutable_pin", ["check_manifest_immutable.py", "--pin"], ["--run"]),
]


def _run(check_name: str, script_args: list[str], run_args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, str(THIS_DIR / script_args[0]), *script_args[1:], *run_args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        required=False,
        help="Pipeline run id (directory under .agent-runs/).",
    )
    args = parser.parse_args()

    if not args.run:
        print("run_preflight: no --run argument; no-op outside a pipeline run.")
        return 0

    run_args = ["--run", args.run]

    results: list[tuple[str, bool, str]] = []
    for name, script_args, _ in CHECKS:
        passed, output = _run(name, script_args, run_args)
        results.append((name, passed, output))
        # Pin failures are non-blocking; the run can continue but the
        # --check counterpart later will tell us if drift happened.
        if not passed and name != "check_manifest_immutable_pin":
            # Short-circuit on hard failures so later checks don't
            # confuse the failure output. But still print what we have.
            break

    print("=" * 64)
    print("Preflight checks (v1.2.0+)")
    print("=" * 64)
    for name, passed, output in results:
        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {name}")
        if output:
            for line in output.splitlines():
                print(f"  {line}")

    failed = [name for name, passed, _ in results if not passed]
    print()
    print("-" * 64)
    if failed:
        print(f"PREFLIGHT: {len(failed)} CHECK(S) FAILED")
        for name in failed:
            print(f"  - {name}")
        print(
            "\nThe research stage will NOT run. Fix the manifest or the "
            "control-plane / authorizing-source citation, then re-invoke."
        )
        return 1

    print("PREFLIGHT: ALL CHECKS PASSED")
    print(
        "  manifest schema OK; advances_target aligned with control plane; "
        "paths resolve; SHA pinned for cross-stage integrity check."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
