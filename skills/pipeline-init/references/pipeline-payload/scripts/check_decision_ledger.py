#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate Agent Pipeline decision-ledger.ndjson files.

Ported from agent-pipeline-codex v0.9.0 (scripts/check_decision_ledger.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - source-tree test import
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)
RUN_DIR = REPO_ROOT / ".agent-runs"
LEDGER_SCHEMA_VERSION = "1"
REQUIRED_FIELDS = {
    "allowed": bool,
    "intent": str,
    "claimed_stop_condition": str,
    "reason": str,
    "timestamp": str,
}
OPTIONAL_STRING_FIELDS = {
    "required_next_action",
    "continuing_to",
    "state_path",
    "schema_version",
}


def _ledger_path(run: str | None, ledger: str | None) -> Path:
    if ledger:
        return Path(ledger).expanduser().resolve()
    if run:
        return RUN_DIR / run / "decision-ledger.ndjson"
    raise ValueError("provide --run <run-id> or --ledger <path>")


def validate_ledger(path: Path) -> list[str]:
    violations: list[str] = []
    if not path.exists():
        return [f"ledger not found at {path}"]

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        return [f"ledger is empty at {path}"]

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            violations.append(f"line {index}: blank lines are not valid NDJSON entries")
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            violations.append(f"line {index}: invalid JSON - {exc}")
            continue
        if not isinstance(row, dict):
            violations.append(f"line {index}: entry must be a JSON object")
            continue

        for field, expected_type in REQUIRED_FIELDS.items():
            value = row.get(field)
            if not isinstance(value, expected_type):
                violations.append(
                    f"line {index}: `{field}` must be {expected_type.__name__}"
                )

        for field in OPTIONAL_STRING_FIELDS:
            if field in row and not isinstance(row[field], str):
                violations.append(f"line {index}: `{field}` must be str when present")

        schema_version = str(row.get("schema_version", LEDGER_SCHEMA_VERSION))
        if schema_version != LEDGER_SCHEMA_VERSION:
            violations.append(
                f"line {index}: unsupported schema_version `{schema_version}`; expected `{LEDGER_SCHEMA_VERSION}`"
            )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 3.0.1")
    parser.add_argument("--run", help="Pipeline run id under .agent-runs/.")
    parser.add_argument("--ledger", help="Direct path to decision-ledger.ndjson.")
    args = parser.parse_args()

    try:
        path = _ledger_path(args.run, args.ledger)
    except ValueError as exc:
        print(f"check_decision_ledger: FAIL - {exc}", file=sys.stderr)
        return 2

    violations = validate_ledger(path)
    if violations:
        print("check_decision_ledger: FAIL")
        print(f"  ledger: {path}")
        print("  violations:")
        for violation in violations:
            print(f"    - {violation}")
        return 1

    print(f"check_decision_ledger: PASS - {path} is valid schema v{LEDGER_SCHEMA_VERSION} NDJSON.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
