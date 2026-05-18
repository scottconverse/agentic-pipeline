#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Critic evidence enforcement.

The critic role walks six adversarial lenses (UX, Tests, Engineering,
Docs, QA, Performance) and produces a per-lens block of findings. The
critic.md role file requires each lens to be walked explicitly — but
nothing today enforces that "no findings against this lens" claims are
backed by actual evidence (what was grepped, what was read, what was
compared).

This script grep-validates the critic's output (`critic-report.md`).
Each lens section must contain:

  1. A heading line like `### UX` or `### UX Lens` or `## UX —`.
  2. Findings under it (one or more `- ` bullets), OR
  3. A subsection labeled `**Evidence:**` / `Evidence:` / `### Evidence`
     that contains at least one CITATION — either a file path with
     extension, a command in backticks, or a `grep`/`rg` invocation.

Rubber-stamp critic output ("UX: no findings, work doesn't touch UI")
without any citation fails the gate.

Usage:

    python scripts/check_critic_evidence.py --run <run-id>
    python scripts/check_critic_evidence.py --report path/to/critic-report.md

Exit codes:

    0  — every lens has either findings or evidence citations
    1  — at least one lens has neither
    2  — critic-report.md not found
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


# Lens names the critic must walk. Order doesn't matter for the check;
# all six must be PRESENT and each must have findings or evidence.
REQUIRED_LENSES = ("UX", "Tests", "Engineering", "Docs", "QA", "Performance")

LENS_HEADING_PATTERN = re.compile(
    r"^#{2,4}\s+(?P<lens>UX|Tests|Engineering|Docs|QA|Performance)\b.*$",
    re.IGNORECASE,
)

FINDING_BULLET_PATTERN = re.compile(r"^\s*-\s+.+$")
EVIDENCE_HEADING_PATTERN = re.compile(
    r"^(?:#{2,5}\s+Evidence|\s*\*\*Evidence:?\*\*|\s*Evidence:)\s*$",
    re.IGNORECASE,
)

# Citation heuristics: anything that suggests the critic actually looked
# at concrete artifacts.
CITATION_PATTERNS = (
    re.compile(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5}(?::\d+)?"),  # file path, optionally :line
    re.compile(r"`(?:grep|rg|cat|head|tail|git|gh|npm|pytest|ruff|mypy)\b[^`]*`"),
    re.compile(r"`\$\s+[^`]+`"),  # `$ command`
    re.compile(r"line\s+\d+", re.IGNORECASE),
)


@dataclass
class LensSection:
    name: str
    start_line: int
    end_line: int
    has_findings: bool
    evidence_block_present: bool
    citations_found: list[str]


def _split_lens_sections(report_text: str) -> list[LensSection]:
    lines = report_text.splitlines()
    sections: list[LensSection] = []
    current: dict | None = None

    def close_current(end_line: int) -> None:
        nonlocal current
        if current is None:
            return
        sections.append(
            LensSection(
                name=current["name"],
                start_line=current["start"],
                end_line=end_line,
                has_findings=current["has_findings"],
                evidence_block_present=current["evidence"],
                citations_found=current["citations"],
            )
        )
        current = None

    in_evidence_block = False

    for i, line in enumerate(lines):
        m = LENS_HEADING_PATTERN.match(line)
        if m:
            close_current(i - 1)
            # Normalize lens name to the canonical form from REQUIRED_LENSES
            # (preserves "UX" / "QA" capitalization rather than .capitalize()
            # which would lowercase the second letter).
            extracted = m.group("lens")
            canonical = next(
                (lens for lens in REQUIRED_LENSES if lens.lower() == extracted.lower()),
                extracted,
            )
            current = {
                "name": canonical,
                "start": i,
                "has_findings": False,
                "evidence": False,
                "citations": [],
            }
            in_evidence_block = False
            continue
        if current is None:
            continue
        # Detect generic heading that closes the lens section
        if re.match(r"^#{1,4}\s+", line):
            # Only close if it's a heading NOT for another lens
            if not LENS_HEADING_PATTERN.match(line):
                close_current(i - 1)
                in_evidence_block = False
                continue
        if EVIDENCE_HEADING_PATTERN.match(line):
            current["evidence"] = True
            in_evidence_block = True
            continue
        if FINDING_BULLET_PATTERN.match(line):
            current["has_findings"] = True
        # Look for citations everywhere in the lens section (not just evidence block)
        for pat in CITATION_PATTERNS:
            for hit in pat.findall(line):
                if isinstance(hit, tuple):  # some regex returns groups
                    hit = hit[0] if hit else ""
                if hit and hit not in current["citations"]:
                    current["citations"].append(hit)

    close_current(len(lines) - 1)
    return sections


def evaluate(report_path: Path) -> tuple[list[str], list[LensSection]]:
    """Validate the critic report's evidence structure.

    Returns (missing_or_unsubstantiated, all_lens_sections).
    """
    text = report_path.read_text(encoding="utf-8", errors="replace")
    sections = _split_lens_sections(text)
    by_name = {s.name.lower(): s for s in sections}
    findings: list[str] = []
    for required in REQUIRED_LENSES:
        s = by_name.get(required.lower())
        if s is None:
            findings.append(f"missing lens section: {required}")
            continue
        # A lens passes if either:
        #   - has at least one finding bullet, OR
        #   - has at least one citation anywhere in the section
        if not s.has_findings and not s.citations_found:
            findings.append(
                f"lens '{s.name}' has neither findings nor citation evidence "
                f"(lines {s.start_line + 1}-{s.end_line + 1})"
            )
    return findings, sections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_critic_evidence",
        description="Verify the critic report has evidence (findings or citations) for every lens.",
    )
    parser.add_argument("--run", help="Pipeline run id (directory under .agent-runs/).")
    parser.add_argument("--report", type=Path, help="Path to critic-report.md directly.")
    parser.add_argument(
        "--version", action="version", version="check_critic_evidence 1.2.0"
    )
    args = parser.parse_args(argv)

    repo_root = find_repo_root(__file__)
    if args.report:
        report_path = args.report.resolve()
    elif args.run:
        report_path = (repo_root / ".agent-runs" / args.run / "critic-report.md").resolve()
    else:
        return 0

    if not report_path.exists():
        print(f"ERROR: critic-report.md not found at: {report_path}", file=sys.stderr)
        return 2

    findings, sections = evaluate(report_path)
    if not findings:
        print(f"OK: critic report has evidence for all {len(REQUIRED_LENSES)} required lenses.")
        for s in sections:
            cite_count = len(s.citations_found)
            print(
                f"  {s.name}: findings={s.has_findings}  "
                f"evidence_block={s.evidence_block_present}  citations={cite_count}"
            )
        return 0

    print("CRITIC_EVIDENCE_MISSING — critic report fails evidence check:\n", file=sys.stderr)
    for f in findings:
        print(f"  - {f}", file=sys.stderr)
    print(
        "\nEvery lens must include EITHER:\n"
        "  (a) at least one finding bullet (`- ...`), OR\n"
        "  (b) at least one citation: a file path (e.g. `src/auth.py:42`), "
        "a verifying command in backticks (`` `grep foo bar.py` ``), or a "
        "specific line reference.\n\n"
        "Per-lens 'no findings' claims without citations are rubber-stamps. "
        "Critic.md requires evidence for every zero-finding lens.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
