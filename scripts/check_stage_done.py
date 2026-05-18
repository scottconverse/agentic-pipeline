#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""STAGE_DONE marker enforcement.

v1.1.1 open followup #1 (now closed by v1.2.0): role files describe
the STAGE_DONE marker but don't enforce its presence. Haiku silently
drops the marker line; Sonnet honors it. This script makes the marker
machine-checkable so we can tell when a stage didn't actually complete
even though its output file is present.

The contract:

Each non-pipeline stage role (researcher, planner, test-writer,
executor, verifier, drift-detector, critic, manager) MUST write a line
to `.agent-runs/<run-id>/run.log` of the form:

    STAGE_DONE: <stage_name>

where <stage_name> matches one of the stage names declared in the
pipeline yaml. The line should be the last thing the role writes for
that stage.

This script reads:
  - The pipeline yaml referenced by manifest.pipeline_run.type
  - The run.log
  - Verifies a STAGE_DONE line exists for every COMPLETED stage

Usage:

    python scripts/check_stage_done.py --run <run-id>
    python scripts/check_stage_done.py --run <run-id> --through <stage_name>

  --through <stage_name>  : only require STAGE_DONE for stages up to and
                            including <stage_name>. Used mid-run to
                            verify progress without requiring all stages.

Exit codes:

    0  — all expected STAGE_DONE markers present
    1  — at least one expected STAGE_DONE marker is missing
    2  — manifest/pipeline-yaml not found or malformed
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root

STAGE_DONE_PATTERN = re.compile(r"^\s*STAGE_DONE:\s*(?P<stage>[a-zA-Z][a-zA-Z0-9_\-]*)\s*$")

# Pipeline stages owned by LLM roles (vs the orchestrator's "pipeline" role).
# Only these need STAGE_DONE; pipeline-owned stages don't (the orchestrator
# logs its own transitions).
LLM_ROLE_STAGES = {
    "manifest",
    "research",
    "plan",
    "test-write",
    "execute",
    "verify",
    "drift-detect",
    "critique",
    "manager",
}


def _read_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML is required for check_stage_done.py")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _stages_from_pipeline_yaml(pipeline_yaml: Path) -> list[tuple[str, str]]:
    """Return list of (stage_name, role) from the pipeline yaml."""
    data = _read_yaml(pipeline_yaml)
    stages = data.get("stages") or data.get("pipeline_stages") or []
    if not isinstance(stages, list):
        return []
    out: list[tuple[str, str]] = []
    for s in stages:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        role = s.get("role", "")
        if isinstance(name, str):
            out.append((name, str(role)))
    return out


def _expected_llm_stages(stages: list[tuple[str, str]], through: str | None) -> list[str]:
    """Filter to LLM-owned stages, optionally truncated at `through`."""
    expected: list[str] = []
    for name, role in stages:
        if name in LLM_ROLE_STAGES or role not in ("pipeline", "human"):
            expected.append(name)
            if through and name == through:
                break
    return expected


def _markers_in_run_log(run_log: Path) -> set[str]:
    if not run_log.exists():
        return set()
    found: set[str] = set()
    for line in run_log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = STAGE_DONE_PATTERN.match(line)
        if m:
            found.add(m.group("stage"))
    return found


def evaluate(
    run_id: str,
    repo_root: Path,
    through: str | None = None,
) -> tuple[list[str], list[str], str]:
    """Run STAGE_DONE check for a run.

    Returns (missing_markers, found_markers, run_log_path_str).
    """
    run_dir = (repo_root / ".agent-runs" / run_id).resolve()
    manifest_path = run_dir / "manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = _read_yaml(manifest_path)
    pipeline_run = manifest.get("pipeline_run", {}) if isinstance(manifest, dict) else {}
    pipeline_type = pipeline_run.get("type", "feature")
    pipeline_yaml = (repo_root / ".pipelines" / f"{pipeline_type}.yaml").resolve()
    if not pipeline_yaml.exists():
        raise FileNotFoundError(f"pipeline yaml not found: {pipeline_yaml}")
    stages = _stages_from_pipeline_yaml(pipeline_yaml)
    expected = _expected_llm_stages(stages, through)
    run_log = run_dir / "run.log"
    found = _markers_in_run_log(run_log)
    missing = [s for s in expected if s not in found]
    return missing, sorted(found), str(run_log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_stage_done",
        description="Verify STAGE_DONE markers are present in run.log for completed stages.",
    )
    parser.add_argument("--run", required=False, help="Pipeline run id (directory under .agent-runs/).")
    parser.add_argument(
        "--through",
        help="Only require markers for stages up to and including this stage name.",
    )
    parser.add_argument(
        "--version", action="version", version="check_stage_done 1.2.0"
    )
    args = parser.parse_args(argv)

    if not args.run:
        return 0

    repo_root = find_repo_root(__file__)
    try:
        missing, found, run_log_path = evaluate(args.run, repo_root, args.through)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if not missing:
        scope = f" (through stage '{args.through}')" if args.through else ""
        print(f"OK: all expected STAGE_DONE markers present{scope}.")
        if found:
            print(f"  found: {', '.join(found)}")
        return 0

    print("STAGE_DONE_MISSING: the following stages have no STAGE_DONE marker in:", file=sys.stderr)
    print(f"  {run_log_path}\n", file=sys.stderr)
    for stage in missing:
        print(f"  - missing: STAGE_DONE: {stage}", file=sys.stderr)
    if found:
        print(f"\n  (found markers: {', '.join(found)})", file=sys.stderr)
    print(
        "\nA missing STAGE_DONE marker means the stage's role finished without "
        "writing the required completion line. Either the role drifted "
        "(skipped the marker) or the stage didn't actually finish.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
