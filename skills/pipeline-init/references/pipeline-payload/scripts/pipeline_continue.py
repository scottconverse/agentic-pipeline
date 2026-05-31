#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Print the next executable action for the active Agent Pipeline run.

Ported from agent-pipeline-codex v0.9.0 (scripts/pipeline_continue.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scripts.policy_utils import find_repo_root
    from scripts.stop_validator import active_state_files, validate_state_file
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    try:
        from policy_utils import find_repo_root  # type: ignore
        from stop_validator import active_state_files, validate_state_file  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover - copied project package import
        from scripts.policy.policy_utils import find_repo_root  # type: ignore
        from scripts.policy.stop_validator import (  # type: ignore
            active_state_files,
            validate_state_file,
        )


def next_action(run_dir: Path) -> tuple[int, str]:
    states = active_state_files(run_dir)
    if not states:
        return (
            1,
            f"pipeline_continue: BLOCK\n  reason: no active run found under {run_dir}",
        )

    blocked = []
    allowed = []
    for path in states:
        result = validate_state_file(path)
        if not result.allowed:
            blocked.append(
                (
                    path,
                    result.reason
                    + "; continue_to="
                    + result.continuing_to
                    + "; next_required_action="
                    + result.next_required_action,
                )
            )
        else:
            allowed.append((path, result.stop_condition))

    if blocked:
        lines = ["pipeline_continue: CONTINUE"]
        for path, reason in blocked:
            lines.append(f"  - {path}: {reason}")
        return 1, "\n".join(lines)

    lines = ["pipeline_continue: STOP_ALLOWED"]
    for path, stop_condition in allowed:
        lines.append(f"  - {path}: stop_condition={stop_condition}")
    return 0, "\n".join(lines)


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
    args = parser.parse_args()

    code, message = next_action(Path(args.run_dir))
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
