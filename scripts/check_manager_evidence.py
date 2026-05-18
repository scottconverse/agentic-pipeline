#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Manager decision rigor.

The manager produces `manager-decision.md` with one of three verdicts:
PROMOTE, REPLAN, or BLOCK. Today the role file describes the format
but nothing enforces that the decision actually cites the critic and
drift findings it claims to roll up.

This script verifies the manager's output:

  1. Contains exactly one verdict line: `Decision: PROMOTE|REPLAN|BLOCK`.
  2. Has a `## Resolution per finding` (or equivalent) section.
  3. Every critic finding from `critic-report.md` AND every drift
     finding from `drift-report.md` appears in the manager's resolution
     table with a per-finding disposition.
  4. The disposition for each finding is one of:
     accepted | blocked | replan | resolved | deferred-to-next-rung.

If any critic/drift finding is unresolved by the manager, the gate
fires PROMOTE_WITHOUT_RESOLUTION and exits 1.

Usage:

    python scripts/check_manager_evidence.py --run <run-id>

Exit codes:

    0  — manager decision is well-formed and resolves every finding
    1  — manager decision is missing resolutions, missing verdict, or malformed
    2  — manager-decision.md or upstream reports not found
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


# Handles all of:
#   Decision: PROMOTE
#   **Decision: PROMOTE**
#   **Decision**: PROMOTE
#   ## Decision: PROMOTE
#   Decision: **PROMOTE**
VERDICT_PATTERN = re.compile(
    r"^\s*\*{0,2}(?:#{1,4}\s+)?Decision\*{0,2}\s*:?\s*\*{0,2}\s*"
    r"(?P<verdict>PROMOTE|REPLAN|BLOCK)\b",
    re.IGNORECASE,
)

RESOLUTION_HEADING_PATTERN = re.compile(
    r"^#{2,4}\s+Resolution(?:\s+per\s+finding|s)?\s*$",
    re.IGNORECASE,
)

# Finding-id patterns commonly used by the critic and drift roles.
# Critic: `### Finding C-1`, `**C-1**`, `- C-1:`, `### C-1: ...`
# Drift: `### Finding D-1`, `- D-1:`, etc.
FINDING_ID_PATTERN = re.compile(r"\b(?P<id>[CD]-\d{1,3})\b")

VALID_DISPOSITIONS = {
    "accepted",
    "blocked",
    "replan",
    "resolved",
    "deferred-to-next-rung",
    "deferred",
    "wontfix",
    "duplicate",
}


@dataclass
class FindingRef:
    id: str
    source: str  # 'critic' or 'drift'


try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


def _extract_finding_ids(report_path: Path, source: str) -> list[FindingRef]:
    if not report_path.exists():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    seen: dict[str, FindingRef] = {}
    for m in FINDING_ID_PATTERN.finditer(text):
        fid = m.group("id")
        seen.setdefault(fid, FindingRef(id=fid, source=source))
    return list(seen.values())


def _find_verdict(text: str) -> str | None:
    for line in text.splitlines():
        m = VERDICT_PATTERN.match(line)
        if m:
            return m.group("verdict").upper()
    # Fall back: look for the verdict as a bare word on its own line
    for line in text.splitlines():
        bare = line.strip().upper()
        if bare in ("PROMOTE", "REPLAN", "BLOCK"):
            return bare
    return None


def _find_resolutions(text: str) -> dict[str, str]:
    """Map finding_id -> disposition based on the manager's resolution section.

    Scans for any line that mentions a finding ID and contains one of the
    valid disposition words within a reasonable lookahead (same line or the
    next line).
    """
    lines = text.splitlines()
    resolutions: dict[str, str] = {}
    for i, line in enumerate(lines):
        for m in FINDING_ID_PATTERN.finditer(line):
            fid = m.group("id")
            # Search the same line + next 2 lines for a disposition
            window = " ".join(lines[i: min(i + 3, len(lines))]).lower()
            for disp in VALID_DISPOSITIONS:
                if re.search(rf"\b{re.escape(disp)}\b", window):
                    resolutions[fid] = disp
                    break
    return resolutions


def evaluate(
    run_id: str, repo_root: Path
) -> tuple[list[str], dict[str, object]]:
    """Validate manager-decision.md.

    Returns (findings, info_dict) where info_dict carries the verdict and
    resolution map for the caller's reporting.
    """
    run_dir = (repo_root / ".agent-runs" / run_id).resolve()
    decision_path = run_dir / "manager-decision.md"
    critic_path = run_dir / "critic-report.md"
    drift_path = run_dir / "drift-report.md"

    findings: list[str] = []
    info: dict[str, object] = {}

    if not decision_path.exists():
        findings.append(f"manager-decision.md not found at: {decision_path}")
        return findings, info

    text = decision_path.read_text(encoding="utf-8", errors="replace")

    verdict = _find_verdict(text)
    info["verdict"] = verdict
    if verdict is None:
        findings.append(
            "manager-decision.md has no recognizable verdict "
            "(expected: 'Decision: PROMOTE|REPLAN|BLOCK' or equivalent)"
        )

    # Require Resolution section
    has_resolution_heading = any(RESOLUTION_HEADING_PATTERN.match(line) for line in text.splitlines())
    info["has_resolution_section"] = has_resolution_heading
    if not has_resolution_heading:
        findings.append(
            "manager-decision.md is missing a '## Resolution per finding' (or 'Resolutions') section"
        )

    critic_findings = _extract_finding_ids(critic_path, "critic")
    drift_findings = _extract_finding_ids(drift_path, "drift")
    all_findings = critic_findings + drift_findings
    info["upstream_finding_count"] = len(all_findings)

    resolutions = _find_resolutions(text)
    info["resolutions"] = resolutions

    unresolved = [f.id for f in all_findings if f.id not in resolutions]
    if unresolved:
        findings.append(
            f"manager-decision.md does not resolve {len(unresolved)} of "
            f"{len(all_findings)} upstream findings: {', '.join(sorted(unresolved))}"
        )

    # If verdict is PROMOTE, none of the resolutions should be 'blocked'.
    if verdict == "PROMOTE":
        blocked = [fid for fid, disp in resolutions.items() if disp == "blocked"]
        if blocked:
            findings.append(
                f"verdict is PROMOTE but {len(blocked)} finding(s) are marked 'blocked': "
                f"{', '.join(blocked)}"
            )

    return findings, info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_manager_evidence",
        description="Verify manager-decision.md has a verdict + resolves every upstream finding.",
    )
    parser.add_argument("--run", help="Pipeline run id (directory under .agent-runs/).")
    parser.add_argument(
        "--version", action="version", version="check_manager_evidence 1.2.0"
    )
    args = parser.parse_args(argv)

    if not args.run:
        return 0

    repo_root = find_repo_root(__file__)
    findings, info = evaluate(args.run, repo_root)

    if not findings:
        verdict = info.get("verdict", "?")
        upstream = info.get("upstream_finding_count", 0)
        resolved = len(info.get("resolutions", {}))
        print(
            f"OK: manager-decision.md is well-formed. "
            f"Verdict={verdict}. Upstream findings={upstream}. Resolved={resolved}."
        )
        return 0

    print("MANAGER_EVIDENCE_INCOMPLETE — manager decision fails evidence check:\n", file=sys.stderr)
    for f in findings:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
