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

# v3.0.0 (Opus 4.8 retarget): tolerant fallbacks. The constants above remain
# the fast/exact path; these accept the same content when a more verbose
# model restyles it (bold dropped, spacing/punctuation drift), without
# loosening the semantic checks (counts must still reconcile and zero blocked).
_READINESS_LINE_RE = re.compile(r"DoD\s+readiness", re.IGNORECASE)
_NOT_READY_RE = re.compile(r"\bNOT[_\s]*READY\b", re.IGNORECASE)
_READY_RE = re.compile(r"\bREADY\b", re.IGNORECASE)


def _readiness_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if _READINESS_LINE_RE.search(ln)]


def _declares_not_ready(text: str) -> bool:
    return any(_NOT_READY_RE.search(ln) for ln in _readiness_lines(text))


def _declares_ready(text: str) -> bool:
    for ln in _readiness_lines(text):
        if _NOT_READY_RE.search(ln):
            continue
        if _READY_RE.search(ln):
            return True
    return False


def _tolerant_checklist(text: str) -> tuple[int, int, int, int] | None:
    """Extract (total, ready, blocked, deferred) from a 'checklist' line,
    tolerating bold/spacing/punctuation drift. Returns None if not all four
    counts resolve on a single checklist line."""
    for ln in text.splitlines():
        if not re.search(r"checklist", ln, re.IGNORECASE):
            continue
        got: dict[str, int] = {}
        for field in ("total", "ready", "blocked", "deferred"):
            m = re.search(rf"(\d+)\s+{field}\b", ln, re.IGNORECASE)
            if m:
                got[field] = int(m.group(1))
        if len(got) == 4:
            return got["total"], got["ready"], got["blocked"], got["deferred"]
    return None


def _run_dir(run_id: str) -> Path:
    return REPO_ROOT / ".agent-runs" / run_id


def check_execute_readiness(run_id: str) -> list[str]:
    run_dir = _run_dir(run_id)
    report = run_dir / "implementation-report.md"
    violations: list[str] = []

    if not report.exists():
        return [f"implementation-report.md missing for run {run_id}"]

    text = report.read_text(encoding="utf-8-sig")
    # Readiness line: exact form first, then tolerant detection.
    if READY_LINE not in text and not _declares_ready(text):
        if NOT_READY_LINE in text or _declares_not_ready(text):
            violations.append(
                "implementation-report.md declares DoD readiness NOT_READY; "
                "continue implementation instead of advancing to policy/verify."
            )
        else:
            violations.append(
                "implementation-report.md missing exact readiness line "
                "`**DoD readiness: READY**` (no tolerant readiness form found either)."
            )

    # Checklist counts: exact regex first, then tolerant scan.
    match = CHECKLIST_RE.search(text)
    counts = (
        (int(match.group("total")), int(match.group("ready")),
         int(match.group("blocked")), int(match.group("deferred")))
        if match
        else _tolerant_checklist(text)
    )
    if counts is None:
        violations.append(
            "implementation-report.md missing parseable checklist line "
            "`**DoD checklist: T total, R ready, B blocked, D deferred**`."
        )
    else:
        total, ready, blocked, deferred = counts
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
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 3.0.1")
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
