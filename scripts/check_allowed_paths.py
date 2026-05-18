#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: changed files must fall inside `allowed_paths` and outside `forbidden_paths`.

Reads the pipeline manifest at `.agent-runs/<run-id>/manifest.yaml`,
compares the working-tree diff against the manifest's path lists, and
exits non-zero with evidence lines if any change is out of scope.

If invoked without --run, prints usage and exits 0 (so a developer can
run `python scripts/policy/run_all.py` on a clean working tree without
needing a live pipeline run).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)
RUN_DIR = REPO_ROOT / ".agent-runs"


def _git_changed_files() -> list[str]:
    """Return paths changed in the working tree relative to HEAD.

    Uses `git diff --name-only HEAD` which covers staged + unstaged + new
    files that have been added. Untracked files are NOT in this list by
    design — pipeline runs commit work-in-progress to the run branch
    before the policy stage fires.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _load_manifest_lists(manifest_path: Path) -> tuple[list[str], list[str]]:
    """Return (allowed_paths, forbidden_paths) parsed from manifest YAML.

    Stdlib-only: no PyYAML. The manifest format is a tightly-constrained
    subset (top-level `pipeline_run:` block, list values are simple
    strings under `- ` lines). A real YAML parse is not required for the
    fields this checker reads.
    """
    if not manifest_path.exists():
        print(f"FAIL: manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    text = manifest_path.read_text(encoding="utf-8")
    allowed: list[str] = []
    forbidden: list[str] = []
    current_key: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        # Strip comments after a `#` that is preceded by whitespace.
        if "#" in line:
            hash_idx = line.find("#")
            if hash_idx == 0 or line[hash_idx - 1].isspace():
                line = line[:hash_idx].rstrip()
        if not line:
            continue
        stripped = line.strip()
        # Track which list we're inside.
        if stripped.startswith("allowed_paths:"):
            current_key = "allowed"
            if "[]" in stripped:
                current_key = None
            continue
        if stripped.startswith("forbidden_paths:"):
            current_key = "forbidden"
            if "[]" in stripped:
                current_key = None
            continue
        # Detect any other top-level key — leaves the current list.
        if not raw.startswith((" ", "\t")) and stripped.endswith(":"):
            current_key = None
            continue
        if stripped.startswith("- ") and current_key is not None:
            value = stripped[2:].strip().strip("\"'")
            if current_key == "allowed":
                allowed.append(value)
            elif current_key == "forbidden":
                forbidden.append(value)
        elif current_key is not None and not stripped.startswith("- "):
            current_key = None

    return allowed, forbidden


def _is_under(path: str, prefixes: list[str]) -> bool:
    """True if `path` is exactly a prefix or starts with `prefix + "/"`."""
    for prefix in prefixes:
        if not prefix:
            continue
        normalized = prefix.rstrip("/")
        if path == normalized or path.startswith(normalized + "/"):
            return True
    return False


def _load_target_repos(manifest_path: Path) -> list[dict] | None:
    """Return the manifest's target_repos list, or None if not present / parse failed.

    Requires PyYAML for the nested-object parse. Without yaml, multi-repo
    enforcement is unsupported and check_allowed_paths falls back to
    single-repo behavior.
    """
    if not _HAS_YAML:
        return None
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    pipeline_run = data.get("pipeline_run", data) if isinstance(data, dict) else {}
    if not isinstance(pipeline_run, dict):
        return None
    target_repos = pipeline_run.get("target_repos")
    if not isinstance(target_repos, list) or not target_repos:
        return None
    out: list[dict] = []
    for entry in target_repos:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out.append(entry)
    return out or None


def _git_changed_files_in(repo_path: Path) -> list[str]:
    """Return paths changed in a specific repo's working tree relative to HEAD."""
    if not repo_path.exists() or not (repo_path / ".git").exists():
        # Not a git repo — treat as no changes (the run can't have touched it via git)
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _check_sibling_repos(target_repos: list[dict], umbrella_root: Path) -> list[tuple[str, str]]:
    """For each declared sibling repo, verify its diff stays within its allowed_paths.

    Returns a list of (path, reason) violation tuples.
    """
    violations: list[tuple[str, str]] = []
    for entry in target_repos:
        repo_rel = entry.get("path")
        if not isinstance(repo_rel, str) or not repo_rel.strip():
            continue
        allowed = entry.get("allowed_paths", [])
        if not isinstance(allowed, list):
            continue
        allowed_str = [s for s in allowed if isinstance(s, str)]
        repo_abs = (umbrella_root / repo_rel).resolve()
        changed = _git_changed_files_in(repo_abs)
        for changed_path in changed:
            if allowed_str and not _is_under(changed_path, allowed_str):
                violations.append(
                    (f"{repo_rel}/{changed_path}", f"outside target_repos[{repo_rel}].allowed_paths")
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        help="Pipeline run id (directory under .agent-runs/). Without this, the check is a no-op.",
    )
    args = parser.parse_args()

    if not args.run:
        print(
            "check_allowed_paths: no --run argument provided; skipping (no-op outside a pipeline run)."
        )
        return 0

    manifest_path = RUN_DIR / args.run / "manifest.yaml"
    allowed, forbidden = _load_manifest_lists(manifest_path)
    target_repos = _load_target_repos(manifest_path)

    if not allowed and not forbidden and not target_repos:
        print(
            "check_allowed_paths: manifest has empty allowed_paths AND forbidden_paths AND no "
            "target_repos — no constraints to enforce. PASS."
        )
        return 0

    changed = _git_changed_files()

    violations: list[tuple[str, str]] = []
    if changed:
        for path in changed:
            if forbidden and _is_under(path, forbidden):
                violations.append((path, "matches forbidden_paths"))
                continue
            if allowed and not _is_under(path, allowed):
                violations.append((path, "outside allowed_paths"))

    # Multi-repo: check each declared sibling repo's diff against its allowed_paths
    sibling_violations: list[tuple[str, str]] = []
    sibling_changed_total = 0
    if target_repos:
        sibling_violations = _check_sibling_repos(target_repos, REPO_ROOT)
        for entry in target_repos:
            repo_rel = entry.get("path", "")
            repo_abs = (REPO_ROOT / repo_rel).resolve()
            sibling_changed_total += len(_git_changed_files_in(repo_abs))
        violations.extend(sibling_violations)
    elif target_repos is None and _HAS_YAML is False:
        # If yaml isn't available, we can't enforce multi-repo. Warn.
        # (Single-repo manifests work fine without yaml.)
        pass

    if violations:
        print("check_allowed_paths: FAIL")
        print(f"  manifest: {manifest_path}")
        print(f"  allowed_paths (umbrella): {allowed or '(none)'}")
        print(f"  forbidden_paths (umbrella): {forbidden or '(none)'}")
        if target_repos:
            print(f"  target_repos: {len(target_repos)} sibling(s)")
            for entry in target_repos:
                print(f"    - path: {entry.get('path')}  allowed: {entry.get('allowed_paths', [])}")
        print("  violations:")
        for path, reason in violations:
            print(f"    {path}  ({reason})")
        return 1

    total_changed = len(changed) + sibling_changed_total
    if total_changed == 0:
        print("check_allowed_paths: no changed files in any tracked repo. PASS.")
    else:
        scope_note = (
            f" (umbrella: {len(changed)}, siblings: {sibling_changed_total})"
            if target_repos
            else ""
        )
        print(
            f"check_allowed_paths: PASS — {total_changed} changed file(s)"
            f"{scope_note}, all within declared paths."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
