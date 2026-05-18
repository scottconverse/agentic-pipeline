#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: the run manifest must satisfy a strict schema.

Fuzzy manifests are the largest single source of agent drift. This check
enforces structural minimums on the manifest so the downstream stages
(researcher, planner, executor, verifier, critic, drift-detector,
auto-promote) have a contract worth enforcing.

Rules enforced:
  - `goal` is a non-empty quoted string of >= MIN_GOAL_CHARS characters.
  - `expected_outputs` has >= 1 entry; each entry is non-empty.
  - `definition_of_done` is a non-empty quoted string of
    >= MIN_DOD_CHARS characters.
  - `non_goals` has >= 1 entry; each entry is non-empty.
  - `rollback_plan` is a non-empty quoted string.
  - When `allowed_paths` contains a "broad" prefix (a top-level
    directory like "src/" with no further specificity),
    `forbidden_paths` must be non-empty. Belt-and-suspenders for
    runs whose scope is wide.
  - `goal` and `definition_of_done` must NOT contain forbidden status
    words (`done`, `complete`, `ready`, `shippable`, `taggable`,
    case-insensitive). These words are forbidden in manifest contracts
    because they import the project's ambient release-gate semantics
    into a run that is not itself a release gate.

If invoked without --run, prints usage and exits 0 (no-op outside a
pipeline run). When run via `auto_promote.py` or `run_all.py` with a
--run argument, all rules are enforced.

The fix from PR #7 (resolve REPO_ROOT for both source and installed
layouts) is applied here as well.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FORBIDDEN_STATUS_WORDS = {"done", "complete", "ready", "shippable", "taggable"}
MIN_GOAL_CHARS = 30
MIN_DOD_CHARS = 80
MIN_ADVANCES_TARGET_CHARS = 8
MIN_OVERRIDE_REASON_CHARS = 60
BROAD_PREFIX_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*/$")
AUTHORIZING_SOURCE_PATTERN = re.compile(
    r"^[^\s:]+(?:\.[A-Za-z0-9]+)?(?::\d+)?$"
)


try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - installed layout
    from scripts.policy_utils import find_repo_root


REPO_ROOT = find_repo_root(__file__)
RUN_DIR = REPO_ROOT / ".agent-runs"


def _read_manifest(manifest_path: Path) -> dict[str, object]:
    """Parse the manifest into a flat dict.

    Stdlib-only minimal YAML parser, matching the conventions used by
    check_allowed_paths.py. Supports:
      - top-level `pipeline_run:` block
      - scalar values: `key: "string"` or `key: bareword`
      - list values: `key:` followed by `  - "item"` lines
      - inline empty lists: `key: []`
      - comments after whitespace + `#`

    Returns a dict keyed by manifest field. List values are list[str].
    Scalar values are str.
    """
    if not manifest_path.exists():
        print(f"check_manifest_schema: FAIL -- manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    text = manifest_path.read_text(encoding="utf-8")
    fields: dict[str, object] = {}
    current_list_key: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if "#" in line:
            hash_idx = line.find("#")
            if hash_idx == 0 or line[hash_idx - 1].isspace():
                line = line[:hash_idx].rstrip()
        if not line:
            continue
        stripped = line.strip()

        # Reset list-accumulation when we hit a new top-level / nested key.
        if stripped.startswith("- ") and current_list_key is not None:
            value = stripped[2:].strip().strip("\"'")
            existing = fields.setdefault(current_list_key, [])
            assert isinstance(existing, list)
            existing.append(value)
            continue

        # Any non-list line ends list accumulation.
        current_list_key = None

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if key in ("pipeline_run",):
            # Top-level block marker; no value.
            continue

        if value == "[]":
            fields[key] = []
            continue
        if value == "":
            current_list_key = key
            fields.setdefault(key, [])
            continue

        # Scalar
        scalar = value.strip("\"'")
        fields[key] = scalar

    return fields


def _is_broad_prefix(prefix: str) -> bool:
    """True if `prefix` is a top-level directory with no further specificity.

    Examples that match:
      - `src/`, `lib/`, `civiccast/`, `app/`

    Examples that do not match:
      - `src/auth/`, `civiccast/stream/`, `civicrecords-ai/civicrecords_ai/`

    A broad prefix authorizes large blast radius; the schema requires
    `forbidden_paths` to be populated in that case.
    """
    normalized = prefix.strip()
    if not normalized.endswith("/"):
        normalized = normalized + "/"
    return bool(BROAD_PREFIX_PATTERN.fullmatch(normalized))


def _check(fields: dict[str, object]) -> list[dict[str, str]]:
    """Apply schema rules. Return a list of violation dicts (empty = pass).

    Each violation has keys:
      - field:   the manifest field that failed
      - problem: one-sentence what's wrong
      - current: short repr of the current value (or "<missing>")
      - suggest: one concrete next action the operator can take
    """
    violations: list[dict[str, str]] = []

    goal = fields.get("goal")
    if not isinstance(goal, str) or len(goal.strip()) < MIN_GOAL_CHARS:
        actual_len = len(goal.strip()) if isinstance(goal, str) else 0
        violations.append(
            {
                "field": "goal",
                "problem": f"too short ({actual_len} chars; minimum {MIN_GOAL_CHARS})",
                "current": _short_repr(goal),
                "suggest": (
                    "describe what the work produces for the user, citing the spec or design note. "
                    "Example: \"Fix the 409 conflict-race on schedule update when the conflicting "
                    "item is cancelled mid-lookup (QA-005).\""
                ),
            }
        )
    else:
        for word in FORBIDDEN_STATUS_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", goal, re.IGNORECASE):
                violations.append(
                    {
                        "field": "goal",
                        "problem": f"contains forbidden status word '{word}'",
                        "current": _short_repr(goal),
                        "suggest": (
                            f"replace '{word}' with a descriptive verb. Manifests must not import "
                            "release-gate semantics into a non-release run."
                        ),
                    }
                )

    dod = fields.get("definition_of_done")
    if not isinstance(dod, str) or len(dod.strip()) < MIN_DOD_CHARS:
        actual_len = len(dod.strip()) if isinstance(dod, str) else 0
        violations.append(
            {
                "field": "definition_of_done",
                "problem": f"too short ({actual_len} chars; minimum {MIN_DOD_CHARS})",
                "current": _short_repr(dod),
                "suggest": (
                    "write one paragraph naming the precise bar the work clears, citing tests, "
                    "specs, or CI gates. The verifier compares the final state to this paragraph "
                    "line-by-line."
                ),
            }
        )
    else:
        for word in FORBIDDEN_STATUS_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", dod, re.IGNORECASE):
                violations.append(
                    {
                        "field": "definition_of_done",
                        "problem": f"contains forbidden status word '{word}'",
                        "current": _short_repr(dod),
                        "suggest": f"replace '{word}' with a descriptive bar (see goal rule).",
                    }
                )

    expected_outputs = fields.get("expected_outputs")
    if not isinstance(expected_outputs, list) or len(expected_outputs) < 1:
        violations.append(
            {
                "field": "expected_outputs",
                "problem": "empty list",
                "current": "[]",
                "suggest": (
                    "add at least one testable output: a file path that must exist, a passing "
                    "test name, a function/class that must be defined, or an HTTP endpoint that "
                    "must respond 2xx."
                ),
            }
        )
    elif any(not (isinstance(item, str) and item.strip()) for item in expected_outputs):
        violations.append(
            {
                "field": "expected_outputs",
                "problem": "contains an empty entry",
                "current": _short_repr(expected_outputs),
                "suggest": "remove the empty entry, or replace it with a real expected output.",
            }
        )

    non_goals = fields.get("non_goals")
    if not isinstance(non_goals, list) or len(non_goals) < 1:
        violations.append(
            {
                "field": "non_goals",
                "problem": "empty list",
                "current": "[]",
                "suggest": (
                    "add at least one explicit out-of-scope item -- e.g. \"Operator UI changes "
                    "(Slice 2)\" or \"Schema migrations (release-engineer only).\" Non-goals are "
                    "what keeps the executor from drifting."
                ),
            }
        )

    rollback_plan = fields.get("rollback_plan")
    if not isinstance(rollback_plan, str) or not rollback_plan.strip():
        violations.append(
            {
                "field": "rollback_plan",
                "problem": "empty",
                "current": "\"\"",
                "suggest": (
                    "name how a revert would happen. For pure code changes: \"git revert "
                    "<commit-sha>; no schema migration.\" For schema changes: name the "
                    "down-migration explicitly."
                ),
            }
        )

    allowed_paths = fields.get("allowed_paths")
    forbidden_paths = fields.get("forbidden_paths")
    if isinstance(allowed_paths, list) and any(
        isinstance(p, str) and _is_broad_prefix(p) for p in allowed_paths
    ):
        if not isinstance(forbidden_paths, list) or len(forbidden_paths) < 1:
            broad = [p for p in allowed_paths if isinstance(p, str) and _is_broad_prefix(p)]
            violations.append(
                {
                    "field": "forbidden_paths",
                    "problem": (
                        f"empty, but allowed_paths includes broad prefix(es) "
                        f"({', '.join(broad)})"
                    ),
                    "current": "[]",
                    "suggest": (
                        "add explicit forbidden_paths to bound the blast radius. Common entries: "
                        "\"docs/adr/\" (ADRs are append-only -- new ADRs are fine, modifying "
                        "existing ones is not), the project's version file (release-engineer only), "
                        "\".github/workflows/\" (CI changes need their own scope)."
                    ),
                }
            )

    # ---------------------------------------------------------------------
    # v1.2.0 priority-drift fields
    # ---------------------------------------------------------------------

    advances_target = fields.get("advances_target")
    override_reason = fields.get("override_active_target")
    has_override = (
        isinstance(override_reason, str)
        and len(override_reason.strip()) >= MIN_OVERRIDE_REASON_CHARS
    )

    if not isinstance(advances_target, str) or len(advances_target.strip()) < MIN_ADVANCES_TARGET_CHARS:
        violations.append(
            {
                "field": "advances_target",
                "problem": (
                    f"missing or too short (minimum {MIN_ADVANCES_TARGET_CHARS} chars; "
                    "v1.2.0+ required field)"
                ),
                "current": _short_repr(advances_target),
                "suggest": (
                    "set advances_target to the exact 'Active target:' string from your project's "
                    "control plane (e.g. .agent-workflows/PROJECT_CONTROL_PLANE.md). "
                    "Example: \"Installer/macOS certification follow-up\". "
                    "check_active_target.py validates this against the discovered control plane."
                ),
            }
        )

    authorizing_source = fields.get("authorizing_source")
    if not has_override:
        # authorizing_source is required when override is not in use
        if not isinstance(authorizing_source, str) or not authorizing_source.strip():
            violations.append(
                {
                    "field": "authorizing_source",
                    "problem": "missing (v1.2.0+ required field unless override_active_target is set)",
                    "current": _short_repr(authorizing_source),
                    "suggest": (
                        "cite the exact line in your control plane authorizing this work, in the "
                        "format `path/to/control_plane.md:LINE_NO`. Example: "
                        "\".agent-workflows/PROJECT_CONTROL_PLANE.md:83\". "
                        "check_manifest_paths.py verifies the file exists and the line is in range."
                    ),
                }
            )
        elif not AUTHORIZING_SOURCE_PATTERN.match(authorizing_source.strip()):
            violations.append(
                {
                    "field": "authorizing_source",
                    "problem": "format invalid (expected path[:line_number])",
                    "current": _short_repr(authorizing_source),
                    "suggest": (
                        "use the format `path/to/file.md:LINE_NO` or `path/to/file.md`. "
                        "Example: \".agent-workflows/PROJECT_CONTROL_PLANE.md:83\"."
                    ),
                }
            )

    if override_reason is not None and not has_override:
        # Override field is present but insufficient
        actual = len(override_reason.strip()) if isinstance(override_reason, str) else 0
        violations.append(
            {
                "field": "override_active_target",
                "problem": (
                    f"override reason too short ({actual} chars; "
                    f"minimum {MIN_OVERRIDE_REASON_CHARS} for ~two sentences)"
                ),
                "current": _short_repr(override_reason),
                "suggest": (
                    "if you genuinely need to bypass the active-target check, write a 2+ sentence "
                    "rationale here. The override is logged to "
                    ".agent-workflows/scope-overrides.md and surfaces at the manifest gate for "
                    "explicit OVERRIDE-CONFIRMED. Empty / short reasons fail closed by design."
                ),
            }
        )

    # ---------------------------------------------------------------------
    # v1.2.0 multi-repo (target_repos) — optional
    # ---------------------------------------------------------------------

    # ---------------------------------------------------------------------
    # v1.2.1 autonomous-mode fields
    # ---------------------------------------------------------------------

    gate_policy = fields.get("gate_policy")
    autonomous_grant = fields.get("autonomous_grant")

    if gate_policy is not None and gate_policy not in ("human", "autonomous", "", None):
        violations.append(
            {
                "field": "gate_policy",
                "problem": f"unknown value {gate_policy!r}; expected 'human' or 'autonomous'",
                "current": _short_repr(gate_policy),
                "suggest": (
                    "set to 'human' (default; three gates require chat-APPROVE) or "
                    "'autonomous' (requires a valid autonomous_grant; gates marked "
                    "v1.3.0: modal gates fire via AskUserQuestion)."
                ),
            }
        )

    if isinstance(gate_policy, str) and gate_policy == "autonomous":
        if not isinstance(autonomous_grant, str) or not autonomous_grant.strip():
            violations.append(
                {
                    "field": "autonomous_grant",
                    "problem": "missing (required when gate_policy=autonomous)",
                    "current": _short_repr(autonomous_grant),
                    "suggest": (
                        "set to the path of your grant file under "
                        ".agent-workflows/autonomous-grants/. Ask Claude in chat to "
                        "create the grant — the chat command writes the file."
                    ),
                }
            )

    target_repos = fields.get("target_repos")
    if target_repos is not None:
        if not isinstance(target_repos, list):
            violations.append(
                {
                    "field": "target_repos",
                    "problem": "must be a list (or omitted entirely for single-repo runs)",
                    "current": _short_repr(target_repos),
                    "suggest": (
                        "for multi-repo runs, use: target_repos:\n"
                        "  - path: ../sibling-repo\n"
                        "    allowed_paths:\n"
                        "      - relative/path/in/sibling/\n"
                        "for single-repo runs, omit this field entirely."
                    ),
                }
            )
        else:
            for idx, entry in enumerate(target_repos):
                if not isinstance(entry, dict):
                    violations.append(
                        {
                            "field": f"target_repos[{idx}]",
                            "problem": "must be an object with `path` and `allowed_paths`",
                            "current": _short_repr(entry),
                            "suggest": "use the documented multi-repo shape (path + allowed_paths).",
                        }
                    )
                    continue
                repo_path = entry.get("path")
                if not isinstance(repo_path, str) or not repo_path.strip():
                    violations.append(
                        {
                            "field": f"target_repos[{idx}].path",
                            "problem": "missing or empty",
                            "current": _short_repr(repo_path),
                            "suggest": (
                                "set to the relative path of the sibling repo, e.g. "
                                "\"../civicrecords-ai\" or \"../civicclerk\"."
                            ),
                        }
                    )
                repo_allowed = entry.get("allowed_paths")
                if not isinstance(repo_allowed, list) or len(repo_allowed) < 1:
                    violations.append(
                        {
                            "field": f"target_repos[{idx}].allowed_paths",
                            "problem": "must be a non-empty list",
                            "current": _short_repr(repo_allowed),
                            "suggest": (
                                "list the path prefixes inside this sibling repo that the run "
                                "may touch. Empty = no work in this repo, which makes the entry "
                                "pointless."
                            ),
                        }
                    )

    return violations


def _short_repr(value: object, max_len: int = 80) -> str:
    """Render a value short enough to fit on one line of an error message."""
    if value is None:
        return "<missing>"
    text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 1.2.1")
    parser.add_argument(
        "--run",
        help="Pipeline run id (directory under .agent-runs/). Without this, the check is a no-op.",
    )
    args = parser.parse_args()

    if not args.run:
        print(
            "check_manifest_schema: no --run argument provided; skipping (no-op outside a pipeline run)."
        )
        return 0

    manifest_path = RUN_DIR / args.run / "manifest.yaml"
    fields = _read_manifest(manifest_path)
    violations = _check(fields)

    if violations:
        # v1.0 error shape: every violation surfaces with what / where /
        # current / suggestion, so the orchestrator can translate
        # directly into the standard chat-message failure shape without
        # post-processing.
        print("Manifest validation FAILED.")
        print(f"  Manifest: {manifest_path.relative_to(REPO_ROOT)}")
        print(f"  Violations: {len(violations)}")
        print()
        for i, v in enumerate(violations, start=1):
            print(f"  [{i}/{len(violations)}] Field: {v['field']}")
            print(f"        Problem: {v['problem']}")
            print(f"        Current: {v['current']}")
            print(f"        Suggestion: {v['suggest']}")
            print()
        print(
            f"  Edit {manifest_path.relative_to(REPO_ROOT)} to fix the violations above,"
        )
        print(f"  then re-run /run resume {args.run}.")
        return 1

    print(
        f"check_manifest_schema: PASS -- manifest at {manifest_path.relative_to(REPO_ROOT)} satisfies the v1.0 schema."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
