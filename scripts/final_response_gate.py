#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail closed when an authorized Agent Pipeline run must continue.

This is the executable pre-final gate. Unlike
``check_pipeline_control_loop.py --run <run-id>``, this script discovers active
control-state files on its own and blocks a final response whenever any active
run records ``final_response_allowed: false``.

Ported from agent-pipeline-codex v0.9.0 (scripts/final_response_gate.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scripts.policy_utils import find_repo_root
    from scripts.stop_validator import (
        StopValidation as GateResult,
        validate_all_active_stops,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    try:
        from policy_utils import find_repo_root
        from stop_validator import (
            StopValidation as GateResult,
            validate_all_active_stops,
        )
    except ModuleNotFoundError:  # pragma: no cover - copied project package import
        from scripts.policy.policy_utils import find_repo_root
        from scripts.policy.stop_validator import (
            StopValidation as GateResult,
            validate_all_active_stops,
        )


def evaluate_final_response_gate(
    run_dir: Path, require_active_run: bool = False
) -> list[GateResult]:
    return validate_all_active_stops(run_dir, require_active_run=require_active_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", action="version", version="agent-pipeline-claude 3.0.1"
    )
    parser.add_argument(
        "--run-dir",
        default=str(find_repo_root(__file__) / ".agent-runs"),
        help="Directory containing run subdirectories. Defaults to .agent-runs in this repo.",
    )
    parser.add_argument(
        "--require-active-run",
        action="store_true",
        help="Fail if no active pipeline run is found.",
    )
    args = parser.parse_args()

    results = evaluate_final_response_gate(Path(args.run_dir), args.require_active_run)
    blocked = [result for result in results if not result.allowed]

    if blocked:
        print("final_response_gate: BLOCK")
        for result in blocked:
            location = f" ({result.state_path})" if result.state_path else ""
            print(f"  - {result.reason}{location}")
            if result.continuing_to:
                print(f"    continuing_to: {result.continuing_to}")
            if result.next_required_action:
                print(f"    next_required_action: {result.next_required_action}")
        return 1

    print("final_response_gate: ALLOW")
    for result in results:
        location = f" ({result.state_path})" if result.state_path else ""
        print(f"  - {result.reason}{location}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
