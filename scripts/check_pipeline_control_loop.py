#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate Agent Pipeline continuation control-state artifacts.

The pipeline continuation rule is mechanical:

The agent may not end a turn during an authorized pipeline run unless it
records a valid stop condition from the allowed list.

This script validates that rule against the durable
``.agent-runs/<run-id>/active-control-state.md`` artifact and can also scan
reports for unresolved ``Open Caveats / Release Risks`` sections.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_pipeline_control_loop.py).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - package import in tests
    from scripts.policy_utils import find_repo_root

VALID_STOP_CONDITIONS = {
    "human_approval_gate",
    "failed_gate_needs_user_direction",
    "destructive_action",
    "credential_or_secret_required",
    "scope_conflict",
    "external_system_unavailable_after_retry",
    "user_explicitly_paused_or_stopped",
}

INVALID_STOP_CONDITIONS = {
    "successful_push",
    "green_ci",
    "recommended_next_action",
    "open_caveats",
    "release_or_tag_after_gates_pass",
    "pr_draft_status",
    "unverified_blocker_or_risk",
}

REQUIRED_STATE_KEYS = (
    "active_run",
    "current_stage",
    "last_completed_gate",
    "next_required_action",
    "stop_condition",
    "final_response_allowed",
    "continuing_to",
)

SECTION_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
KEY_RE = re.compile(r"^(?:-\s+)?(?P<key>[a-zA-Z0-9_-]+):\s*(?P<value>.*)$")


REPO_ROOT = find_repo_root(__file__)
RUN_DIR = REPO_ROOT / ".agent-runs"


def parse_control_state(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = KEY_RE.match(line.strip())
        if match:
            fields[match.group("key")] = match.group("value").strip()
    return fields


def validate_control_state(fields: dict[str, str]) -> list[str]:
    violations: list[str] = []

    for key in REQUIRED_STATE_KEYS:
        if key not in fields:
            violations.append(f"missing required key `{key}`")

    active_run = fields.get("active_run", "").lower()
    final_allowed = fields.get("final_response_allowed", "").lower()
    stop_condition = fields.get("stop_condition", "")
    continuing_to = fields.get("continuing_to", "")
    next_required_action = fields.get("next_required_action", "")

    if active_run not in {"true", "false"}:
        violations.append("`active_run` must be `true` or `false`")

    if final_allowed not in {"true", "false"}:
        violations.append("`final_response_allowed` must be `true` or `false`")

    if stop_condition in INVALID_STOP_CONDITIONS:
        violations.append(f"`stop_condition` uses invalid stop condition `{stop_condition}`")

    if stop_condition == "none":
        if final_allowed != "false":
            violations.append("`final_response_allowed` must be `false` when `stop_condition` is `none`")
        if not continuing_to:
            violations.append("`continuing_to` is required when `stop_condition` is `none`")
        if not next_required_action:
            violations.append("`next_required_action` is required when `stop_condition` is `none`")
    elif stop_condition:
        if stop_condition not in VALID_STOP_CONDITIONS:
            allowed = ", ".join(sorted(VALID_STOP_CONDITIONS))
            violations.append(f"`stop_condition` must be `none` or one of: {allowed}")
        if final_allowed != "true":
            violations.append(
                "`final_response_allowed` must be `true` when a valid stop condition is recorded"
            )

    if active_run == "true" and final_allowed == "true" and stop_condition == "none":
        violations.append("active runs cannot allow a final response without a valid stop condition")

    return violations


def unresolved_caveats(text: str) -> list[str]:
    """Return caveat lines that are not explicit intentional deferrals."""
    lines = text.splitlines()
    in_caveats = False
    findings: list[str] = []

    for line in lines:
        heading = SECTION_RE.match(line)
        if heading:
            title = heading.group("title").strip().lower()
            in_caveats = title in {"open caveats / release risks", "open caveats", "release risks"}
            continue

        if not in_caveats:
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            continue
        if stripped.startswith(("- ", "* ")):
            body = stripped[2:].strip()
            if not body.startswith("INTENTIONAL DEFERRAL:"):
                findings.append(body)

    return findings


def _print_policy() -> None:
    print("Valid stop conditions:")
    for item in sorted(VALID_STOP_CONDITIONS):
        print(f"  - {item}")
    print("Invalid stop conditions:")
    for item in sorted(INVALID_STOP_CONDITIONS):
        print(f"  - {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", help="Pipeline run id under .agent-runs/.")
    parser.add_argument(
        "--state-file",
        help="Control-state file path. Defaults to .agent-runs/<run>/active-control-state.md.",
    )
    parser.add_argument("--report", action="append", default=[], help="Report file to scan for open caveats.")
    parser.add_argument("--print-policy", action="store_true", help="Print stop-condition policy and exit.")
    args = parser.parse_args()

    if args.print_policy:
        _print_policy()
        return 0

    violations: list[str] = []

    if args.run or args.state_file:
        if args.state_file:
            state_path = Path(args.state_file)
        elif args.run:
            state_path = RUN_DIR / args.run / "active-control-state.md"
        else:
            raise AssertionError("unreachable")

        if not state_path.exists():
            violations.append(f"active control state not found at {state_path}")
        else:
            fields = parse_control_state(state_path.read_text(encoding="utf-8-sig"))
            violations.extend(validate_control_state(fields))

    for report in args.report:
        report_path = Path(report)
        if not report_path.exists():
            violations.append(f"report not found at {report_path}")
            continue
        findings = unresolved_caveats(report_path.read_text(encoding="utf-8-sig"))
        for finding in findings:
            violations.append(
                f"{report_path}: unresolved Open Caveats / Release Risks item: {finding}"
            )

    if violations:
        print("check_pipeline_control_loop: FAIL")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print("check_pipeline_control_loop: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
