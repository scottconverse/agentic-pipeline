#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Manifest filesystem-integrity gate.

A manifest can name file paths and citation references that don't exist
in the repo at the time of the run. The drafter might hallucinate, the
manifest might be stale, or a refactor might have moved a target file.

This script catches that class of error at preflight, BEFORE the
researcher or executor wastes time reasoning about a path that won't
resolve.

What it checks:

  1. Every entry in `allowed_paths` exists as either a file or directory
     prefix in the repo. A prefix that does not exist as a real path is
     either a typo or a stale reference.

  2. Every entry in `target_repos[].path` resolves to a directory if
     multi-repo mode is in use. The repo's `allowed_paths` are then
     verified relative to that repo's root.

  3. Every entry in `expected_outputs` that LOOKS like a file path is
     either an existing file (already there) OR a path within
     `allowed_paths` (will be created during the run). An expected
     output that's neither is a manifest error.

  4. `authorizing_source` (if present, as required by v1.2.0 schema)
     points to a file:line that exists. The file must exist; the line
     number must be within the file's line count.

This script is strict on `allowed_paths` and `target_repos[].path` —
those define the write surface and must be real.

It is lenient on `expected_outputs` — an output can legitimately not
exist yet (the run creates it). Only blocks if the output's directory
prefix isn't under any allowed_path.

Usage:

    python scripts/check_manifest_paths.py --run <run-id>
    python scripts/check_manifest_paths.py --manifest path/to/manifest.yaml

Exit codes:

    0  — all paths/citations resolve correctly
    1  — at least one path/citation does not resolve
    2  — manifest missing or malformed
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


@dataclass
class PathFinding:
    field: str
    value: str
    reason: str


try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


def _read_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required for check_manifest_paths.py. "
            "Install with: pip install pyyaml"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


AUTHORIZING_SOURCE_PATTERN = re.compile(
    r"^(?P<path>[^\s:]+(?:\.[a-z]+)?)(?::(?P<line>\d+))?$"
)


def _check_path_exists(
    repo_root: Path, value: str, must_exist: bool
) -> tuple[bool, str]:
    """Check whether a path or path-prefix exists.

    Returns (ok, reason). For directory-style prefixes (ending in /),
    requires the directory to exist. For non-trailing-slash entries,
    accepts either a file OR a directory at that path.

    If must_exist is False, accepts non-existent paths whose PARENT
    directory exists (the path will be created during the run).
    """
    p = (repo_root / value).resolve()
    # Don't allow escaping the repo root
    try:
        p.relative_to(repo_root)
    except ValueError:
        return False, f"path escapes repo root: {value}"

    if p.exists():
        return True, "exists"

    if must_exist:
        return False, f"path does not exist: {value}"

    # Lenient: accept if parent exists (the run will create it)
    parent = p.parent
    if parent.exists() and parent.is_dir():
        return True, f"will be created (parent exists: {parent.relative_to(repo_root)})"
    return False, f"neither path nor its parent directory exists: {value}"


def _check_authorizing_source(
    repo_root: Path, value: str
) -> tuple[bool, str]:
    """Validate authorizing_source format: 'path/to/file:LINE' or 'path/to/file'.

    File must exist. If a line number is present, it must be within the
    file's line count.
    """
    m = AUTHORIZING_SOURCE_PATTERN.match(value)
    if not m:
        return False, f"authorizing_source format invalid (expected path[:line]): {value}"
    rel_path = m.group("path")
    line_str = m.group("line")
    abs_path = (repo_root / rel_path).resolve()
    try:
        abs_path.relative_to(repo_root)
    except ValueError:
        return False, f"authorizing_source path escapes repo root: {rel_path}"
    if not abs_path.exists() or not abs_path.is_file():
        return False, f"authorizing_source file does not exist: {rel_path}"
    if line_str:
        try:
            line_no = int(line_str)
        except ValueError:
            return False, f"authorizing_source line not an integer: {line_str}"
        line_count = sum(1 for _ in abs_path.open("r", encoding="utf-8", errors="replace"))
        if line_no < 1 or line_no > line_count:
            return False, (
                f"authorizing_source line {line_no} out of range "
                f"(file has {line_count} lines): {rel_path}"
            )
    return True, "exists"


def _output_within_allowed(
    repo_root: Path, output: str, allowed_paths: list[str]
) -> bool:
    """Check if an expected_output's parent path is under any allowed_path."""
    if not allowed_paths:
        return False
    output_norm = output.replace("\\", "/").lstrip("./")
    for allowed in allowed_paths:
        allowed_norm = allowed.replace("\\", "/").lstrip("./")
        # Allow exact match or prefix match (directory-style)
        if output_norm == allowed_norm:
            return True
        if output_norm.startswith(allowed_norm.rstrip("/") + "/"):
            return True
    return False


def evaluate(manifest_path: Path, repo_root: Path) -> list[PathFinding]:
    """Run filesystem integrity checks against a manifest.

    Returns a list of findings. Empty list = all good.
    """
    if not manifest_path.exists():
        return [PathFinding("manifest", str(manifest_path), "manifest file not found")]
    try:
        manifest = _read_yaml(manifest_path)
    except Exception as e:
        return [PathFinding("manifest", str(manifest_path), f"manifest parse error: {e}")]

    pipeline_run = manifest.get("pipeline_run", {}) if isinstance(manifest, dict) else {}
    if not isinstance(pipeline_run, dict):
        return [PathFinding("pipeline_run", "<root>", "pipeline_run section missing or malformed")]

    findings: list[PathFinding] = []

    target_repos = pipeline_run.get("target_repos") or []
    single_repo_allowed = pipeline_run.get("allowed_paths") or []

    # Build the effective (root, allowed_paths) tuples
    repo_scopes: list[tuple[Path, list[str], str]] = []
    if isinstance(target_repos, list) and target_repos:
        for idx, entry in enumerate(target_repos):
            if not isinstance(entry, dict):
                findings.append(
                    PathFinding(
                        f"target_repos[{idx}]",
                        str(entry),
                        "must be an object with `path` and `allowed_paths`",
                    )
                )
                continue
            repo_rel = entry.get("path")
            repo_allowed = entry.get("allowed_paths") or []
            if not isinstance(repo_rel, str) or not repo_rel.strip():
                findings.append(
                    PathFinding(
                        f"target_repos[{idx}].path",
                        str(repo_rel),
                        "target_repos[].path is required and must be a non-empty string",
                    )
                )
                continue
            if not isinstance(repo_allowed, list):
                findings.append(
                    PathFinding(
                        f"target_repos[{idx}].allowed_paths",
                        str(repo_allowed),
                        "target_repos[].allowed_paths must be a list",
                    )
                )
                continue
            # The repo path must exist as a directory
            ok, reason = _check_path_exists(repo_root, repo_rel, must_exist=True)
            if not ok:
                findings.append(
                    PathFinding(f"target_repos[{idx}].path", repo_rel, reason)
                )
                continue
            repo_abs = (repo_root / repo_rel).resolve()
            if not repo_abs.is_dir():
                findings.append(
                    PathFinding(
                        f"target_repos[{idx}].path",
                        repo_rel,
                        f"target_repos[].path must be a directory: {repo_rel}",
                    )
                )
                continue
            repo_scopes.append((repo_abs, repo_allowed, f"target_repos[{idx}]"))
    else:
        repo_scopes.append((repo_root, single_repo_allowed, "manifest"))

    # 1. Verify each allowed_paths entry within each scope
    for scope_root, scope_allowed, scope_name in repo_scopes:
        if not isinstance(scope_allowed, list):
            findings.append(
                PathFinding(
                    f"{scope_name}.allowed_paths",
                    str(scope_allowed),
                    "allowed_paths must be a list",
                )
            )
            continue
        for path_value in scope_allowed:
            if not isinstance(path_value, str):
                findings.append(
                    PathFinding(
                        f"{scope_name}.allowed_paths",
                        str(path_value),
                        "allowed_paths entries must be strings",
                    )
                )
                continue
            ok, reason = _check_path_exists(scope_root, path_value, must_exist=True)
            if not ok:
                findings.append(
                    PathFinding(
                        f"{scope_name}.allowed_paths",
                        path_value,
                        reason,
                    )
                )

    # 2. expected_outputs lenient check (parent under some allowed)
    expected_outputs = pipeline_run.get("expected_outputs") or []
    if isinstance(expected_outputs, list):
        # Flatten allowed paths across all scopes for the "any-scope" check
        flat_allowed: list[str] = []
        for _, scope_allowed, _ in repo_scopes:
            if isinstance(scope_allowed, list):
                flat_allowed.extend(s for s in scope_allowed if isinstance(s, str))
        for output in expected_outputs:
            if not isinstance(output, str):
                continue
            # Heuristic: only treat strings that look like file paths
            # (contain a slash or end in a recognizable extension) as paths.
            looks_like_path = (
                "/" in output
                or "\\" in output
                or re.search(r"\.[a-z0-9]{1,5}$", output)
            )
            if not looks_like_path:
                continue
            if not _output_within_allowed(repo_root, output, flat_allowed):
                findings.append(
                    PathFinding(
                        "expected_outputs",
                        output,
                        "expected_output is not within any declared allowed_paths",
                    )
                )

    # 3. authorizing_source validation
    auth_source = pipeline_run.get("authorizing_source")
    if isinstance(auth_source, str) and auth_source.strip():
        ok, reason = _check_authorizing_source(repo_root, auth_source.strip())
        if not ok:
            findings.append(PathFinding("authorizing_source", auth_source, reason))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_manifest_paths",
        description="Verify the manifest's paths and citations resolve in the filesystem.",
    )
    parser.add_argument("--run", help="Pipeline run id (directory under .agent-runs/).")
    parser.add_argument("--manifest", type=Path, help="Path to a manifest YAML directly.")
    parser.add_argument(
        "--version", action="version", version="check_manifest_paths 1.2.0"
    )
    args = parser.parse_args(argv)

    repo_root = find_repo_root(__file__)

    if args.manifest:
        manifest_path = args.manifest.resolve()
    elif args.run:
        manifest_path = (repo_root / ".agent-runs" / args.run / "manifest.yaml").resolve()
    else:
        return 0  # no-op when invoked without args

    findings = evaluate(manifest_path, repo_root)
    if not findings:
        print("OK: all manifest paths and citations resolve.")
        return 0

    print("MANIFEST_PATH_ERRORS: the following entries do not resolve:\n", file=sys.stderr)
    for f in findings:
        print(f"  - {f.field}: {f.value!r}\n    reason: {f.reason}\n", file=sys.stderr)
    print(
        f"Total path-integrity findings: {len(findings)}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
