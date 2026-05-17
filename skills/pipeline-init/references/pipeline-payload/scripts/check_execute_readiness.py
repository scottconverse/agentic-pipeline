#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Block policy/verify when execute has not proven full DoD readiness.

This check closes the failure mode where an executor finishes a useful slice
of implementation, gets local tests green, and advances to full-rung gates even
though manifest-level product work is still missing.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_execute_readiness.py).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)
READY_LINE = "**DoD readiness: READY**"
NOT_READY_LINE = "**DoD readiness: NOT_READY**"
CHECKLIST_RE = re.compile(
    r"\*\*DoD checklist:\s*(?P<total>\d+)\s+total,\s+"
    r"(?P<ready>\d+)\s+ready,\s+"
    r"(?P<blocked>\d+)\s+blocked,\s+"
    r"(?P<deferred>\d+)\s+deferred\*\*"
)


def _run_dir(run_id: str) -> Path:
    return REPO_ROOT / ".agent-runs" / run_id


def check_execute_readiness(run_id: str) -> list[str]:
    run_dir = _run_dir(run_id)
    report = run_dir / "implementation-report.md"
    violations: list[str] = []

    if not report.exists():
        return [f"implementation-report.md missing for run {run_id}"]

    text = report.read_text(encoding="utf-8-sig")
    if READY_LINE not in text:
        if NOT_READY_LINE in text:
            violations.append(
                "implementation-report.md declares `**DoD readiness: NOT_READY**`; "
                "continue implementation instead of advancing to policy/verify."
            )
        else:
            violations.append(
                "implementation-report.md missing exact readiness line "
                "`**DoD readiness: READY**`."
            )

    match = CHECKLIST_RE.search(text)
    if not match:
        violations.append(
            "implementation-report.md missing parseable checklist line "
            "`**DoD checklist: T total, R ready, B blocked, D deferred**`."
        )
    else:
        total = int(match.group("total"))
        ready = int(match.group("ready"))
        blocked = int(match.group("blocked"))
        deferred = int(match.group("deferred"))
        if total <= 0:
            violations.append("DoD checklist must contain at least one manifest/DoD item.")
        if ready + blocked + deferred != total:
            violations.append(
                "DoD checklist counts do not add up: "
                f"total={total}, ready={ready}, blocked={blocked}, deferred={deferred}."
            )
        if blocked:
            violations.append(
                f"DoD checklist still has {blocked} blocked item(s); "
                "execute is not complete."
            )

    unchecked = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"[-*]\s+\[\s\]\s+", line.strip(), flags=re.IGNORECASE)
    ]
    if unchecked:
        sample = "; ".join(unchecked[:3])
        violations.append(f"implementation-report.md contains unchecked readiness boxes: {sample}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    parser.add_argument("--run", required=True, help="Pipeline run id under .agent-runs/.")
    args = parser.parse_args()

    violations = check_execute_readiness(args.run)
    if violations:
        print("check_execute_readiness: FAIL")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print("check_execute_readiness: PASS - implementation-report.md declares full manifest DoD readiness.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
