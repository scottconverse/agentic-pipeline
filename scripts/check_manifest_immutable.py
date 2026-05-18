#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Manifest immutability gate.

The manifest is the run's contract. Once approved at the manifest gate,
its `allowed_paths`, `forbidden_paths`, `goal`, `advances_target`, and
`authorizing_source` must not change mid-run.

This script implements a two-mode protocol:

  --pin    : Compute SHA-256 of manifest.yaml and write it to
             .agent-runs/<run-id>/manifest.sha. Done once at stage 0.

  --check  : Recompute SHA-256 of manifest.yaml and compare against
             the pinned hash. Mismatch = MANIFEST_MUTATED.

If the manifest legitimately needs to change (e.g., REPLAN at the plan
gate widened the scope), the human must explicitly re-pin via the
orchestrator. Mid-run rewrites by roles are blocked.

Usage:

    python scripts/check_manifest_immutable.py --run <run-id> --pin
    python scripts/check_manifest_immutable.py --run <run-id> --check

Exit codes:

    0  — pin written, or check matches
    1  — MANIFEST_MUTATED: SHA mismatch
    2  — manifest not found or pin file missing in --check mode
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def pin(run_dir: Path) -> tuple[int, str]:
    manifest = run_dir / "manifest.yaml"
    if not manifest.exists():
        return 2, f"manifest not found: {manifest}"
    digest = _sha256(manifest)
    pin_path = run_dir / "manifest.sha"
    pin_path.write_text(digest + "\n", encoding="utf-8")
    return 0, f"OK: manifest SHA-256 pinned to {pin_path}\n  sha256={digest}"


def check(run_dir: Path) -> tuple[int, str]:
    manifest = run_dir / "manifest.yaml"
    pin_path = run_dir / "manifest.sha"
    if not manifest.exists():
        return 2, f"manifest not found: {manifest}"
    if not pin_path.exists():
        return 2, (
            f"pin file not found: {pin_path}\n"
            "Run with --pin at stage 0 to establish the pinned hash."
        )
    expected = pin_path.read_text(encoding="utf-8").strip().split()[0]
    actual = _sha256(manifest)
    if expected == actual:
        return 0, f"OK: manifest SHA matches pin.\n  sha256={actual}"
    return 1, (
        "MANIFEST_MUTATED: manifest.yaml SHA has changed since stage 0 pin.\n"
        f"  expected (pinned): {expected}\n"
        f"  actual:            {actual}\n\n"
        "The manifest is the run's contract. If a legitimate replan widens\n"
        "scope, the human must explicitly re-pin via the orchestrator's\n"
        "manifest-gate REPLAN path. Mid-run rewrites by roles are blocked."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_manifest_immutable",
        description="Pin or check the manifest.yaml SHA-256 to detect mid-run mutation.",
    )
    parser.add_argument("--run", help="Pipeline run id (directory under .agent-runs/).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pin", action="store_true", help="Compute + write the pin file.")
    mode.add_argument(
        "--check", action="store_true", help="Compare current SHA to pinned SHA (default)."
    )
    parser.add_argument(
        "--version", action="version", version="check_manifest_immutable 1.2.0"
    )
    args = parser.parse_args(argv)

    if not args.run:
        return 0

    repo_root = find_repo_root(__file__)
    run_dir = (repo_root / ".agent-runs" / args.run).resolve()
    if not run_dir.exists():
        print(f"ERROR: run directory not found: {run_dir}", file=sys.stderr)
        return 2

    if args.pin:
        rc, msg = pin(run_dir)
    else:
        rc, msg = check(run_dir)

    if rc == 0:
        print(msg)
    else:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
