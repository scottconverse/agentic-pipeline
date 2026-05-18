#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Print a human-readable summary of an Agent Pipeline run.

Ported from agent-pipeline-codex v0.9.0 (scripts/show_run_status.py).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from policy_utils import find_repo_root
    from check_pipeline_control_loop import parse_control_state
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.policy_utils import find_repo_root
    from scripts.check_pipeline_control_loop import parse_control_state


REPO_ROOT = find_repo_root(__file__)
RUN_DIR = REPO_ROOT / ".agent-runs"


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    stage: str
    status: str
    note: str


@dataclass(frozen=True)
class RunLogParseResult:
    entries: list[LogEntry]
    skipped_lines: int


def parse_run_log(path: Path) -> RunLogParseResult:
    if not path.exists():
        return RunLogParseResult([], 0)
    entries: list[LogEntry] = []
    skipped_lines = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        parts = [part.strip() for part in raw.split("|", 3)]
        if len(parts) != 4:
            skipped_lines += 1
            continue
        entries.append(LogEntry(parts[0], parts[1], parts[2], parts[3]))
    return RunLogParseResult(entries, skipped_lines)


def summarize_run(run_dir: Path) -> list[str]:
    lines = [f"show-run-status: {run_dir.name}"]
    parsed_log = parse_run_log(run_dir / "run.log")
    entries = parsed_log.entries
    if entries:
        completed = [entry for entry in entries if entry.status.upper() == "COMPLETE"]
        incomplete = [entry for entry in entries if entry.status.upper() != "COMPLETE"]
        lines.append(f"  stages_complete: {len(completed)}")
        last = entries[-1]
        lines.append(f"  last_event: {last.timestamp} | {last.stage} | {last.status} | {last.note}")
        if incomplete:
            blocked = incomplete[-1]
            lines.append(
                f"  latest_non_complete: {blocked.stage} | {blocked.status} | {blocked.note}"
            )
    else:
        lines.append("  run_log: missing or empty")
    if parsed_log.skipped_lines:
        lines.append(
            f"  run_log_warning: skipped {parsed_log.skipped_lines} malformed line(s)"
        )

    state_path = run_dir / "active-control-state.md"
    if state_path.exists():
        state = parse_control_state(state_path.read_text(encoding="utf-8-sig"))
        lines.append(f"  active_run: {state.get('active_run', '(missing)')}")
        lines.append(f"  current_stage: {state.get('current_stage', '(missing)')}")
        lines.append(f"  final_response_allowed: {state.get('final_response_allowed', '(missing)')}")
        lines.append(f"  stop_condition: {state.get('stop_condition', '(missing)')}")
        if state.get("next_required_action"):
            lines.append(f"  next_required_action: {state['next_required_action']}")
        if state.get("continuing_to"):
            lines.append(f"  continuing_to: {state['continuing_to']}")
    else:
        lines.append("  active_control_state: missing")

    artifacts = sorted(path.name for path in run_dir.iterdir() if path.is_file())
    lines.append(f"  artifacts: {len(artifacts)} file(s)")
    if artifacts:
        lines.append("  artifact_list: " + ", ".join(artifacts))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", required=True, help="Pipeline run id under .agent-runs/.")
    args = parser.parse_args()

    run_dir = RUN_DIR / args.run
    if not run_dir.is_dir():
        print(f"show-run-status: FAIL - run directory not found at {run_dir}", file=sys.stderr)
        return 1
    print("\n".join(summarize_run(run_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
