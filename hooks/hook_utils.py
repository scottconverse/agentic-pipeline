#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Agent Pipeline Claude Code lifecycle hooks.

Ported from agent-pipeline-codex v0.9.0 (hooks/hook_utils.py).
Adapted for claude:
- STALE_STANDALONE_SKILLS lists claude's namespaced skill surface
- NAMESPACED_PREFIX is `agent-pipeline-claude:`
- adds memory-file routing for 5 additional Cowork event types
  (PostToolUseFailure, PreCompact, PostCompact, SubagentStop, SessionEnd)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Claude's namespaced skill surface. If a user prompt names one of these
# bare (without the `agent-pipeline-claude:` prefix), the hook nudges
# them toward the namespaced form so they don't accidentally trigger a
# stale standalone skill of the same name installed under ~/.claude/.
STALE_STANDALONE_SKILLS = {
    "audit-init",
    "intake",
    "pipeline-init",
    "run",
    "show-run-status",
    "grant-autonomous",
    "run-autonomous",
}
NAMESPACED_PREFIX = "agent-pipeline-claude:"
MAX_MEMORY_TEXT = 1200
MAX_HANDOFF_RECORDS = 8

DESTRUCTIVE_PATTERNS = (
    r"\brm\s+-[^\n;|&]*r[^\n;|&]*f\b",
    r"\bRemove-Item\b[^\n;|&]*\b-Recurse\b[^\n;|&]*\b-Force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\b[^\n;|&]*\s--force(?:-with-lease)?\b",
    r"\bnpm\s+publish\b",
    r"\b(drop\s+database|drop\s+table|truncate\s+table)\b",
    r"\bdocker\s+push\b",
    r"\bkubectl\s+(apply|delete|replace)\b",
)
EXTERNAL_OR_RELEASE_PATTERNS = (
    r"\bgit\s+push\b",
    r"\bgh\s+pr\s+(create|merge)\b",
    r"\bgh\s+release\b",
    r"\bcurl\b[^\n;|&]*\s-X\s+(POST|PUT|PATCH|DELETE)\b",
    r"\bInvoke-WebRequest\b[^\n;|&]*\b-Method\s+(Post|Put|Patch|Delete)\b",
)
DEPENDENCY_PATTERNS = (
    r"\bnpm\s+install\b",
    r"\bpip\s+install\b",
    r"\buv\s+add\b",
    r"\bpoetry\s+add\b",
)
SECRET_PATTERNS = (
    r"(?<![\w])(?-i:[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*)\s*=",
    r"\b(cat|type|Get-Content)\b[^\n;|&]*(id_rsa|\.env|credentials|secrets?)\b",
)


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    run_dir: Path
    state_path: Path
    fields: dict[str, str]
    directive_bound: bool
    judge_active: bool
    # Pass 12 (audit Cluster K): bridge model for intake runs. When the
    # intake skill drafts a run but the pipeline hasn't started, the
    # control-state writes `active_run: drafting` (not `true`). Hook
    # callers can downgrade enforcement from deny to warn for drafting
    # runs — operators still see the scope/manifest context, but the
    # gates don't block on artifacts the operator is mid-draft on.
    is_drafting: bool = False


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def repo_root_from_event(event: dict[str, Any]) -> Path:
    """Resolve the project root from a hook event.

    Cowork's Code tab roots cwd at .klodock rather than the picked
    project folder, so prefer CLAUDE_PROJECT_DIR when set. Fall back
    to event.cwd, then to the nearest ancestor with .agent-runs/
    or .claude-plugin/ or .git/.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir).resolve()
    cwd = event.get("cwd") or os.getcwd()
    path = Path(str(cwd)).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".agent-runs").exists() or (candidate / ".claude-plugin").exists() or (candidate / ".git").exists():
            return candidate
    return path


def parse_control_state(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def discover_active_runs(repo_root: Path) -> list[ActiveRun]:
    """Return active runs found under ``.agent-runs/``.

    Pass 12 (audit Cluster K) extends this to also pick up intake-staged
    runs that carry ``active_run: drafting`` in their
    ``active-control-state.md``. Drafting runs are returned with
    ``is_drafting=True`` so hook callers can downgrade enforcement —
    warn-not-block — until the operator promotes the run via
    ``/agent-pipeline-claude:run resume <id>``.
    """
    base = repo_root / ".agent-runs"
    if not base.exists():
        return []
    runs: list[ActiveRun] = []
    for state_path in sorted(base.glob("*/active-control-state.md")):
        fields = parse_control_state(state_path.read_text(encoding="utf-8-sig", errors="replace"))
        state_value = fields.get("active_run", "").lower()
        if state_value not in {"true", "drafting"}:
            continue
        is_drafting = (state_value == "drafting")
        run_dir = state_path.parent
        runs.append(
            ActiveRun(
                run_id=run_dir.name,
                run_dir=run_dir,
                state_path=state_path,
                fields=fields,
                directive_bound=_directive_bound(run_dir),
                judge_active=(repo_root / ".pipelines" / "action-classification.yaml").exists(),
                is_drafting=is_drafting,
            )
        )
    return runs


def latest_run(repo_root: Path) -> Path | None:
    base = repo_root / ".agent-runs"
    if not base.exists():
        return None
    candidates = [path for path in base.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def session_context(runs: list[ActiveRun]) -> str:
    if not runs:
        return ""
    lines = ["Agent Pipeline active run context:"]
    for run in runs:
        state_label = "DRAFTING (intake-staged)" if run.is_drafting else "ACTIVE"
        lines.append(
            "- "
            f"run={run.run_id} [{state_label}]; "
            f"stage={run.fields.get('current_stage', '(unknown)')}; "
            f"next={run.fields.get('next_required_action', '(unspecified)')}; "
            f"continuing_to={run.fields.get('continuing_to', '(unspecified)')}; "
            f"stop_condition={run.fields.get('stop_condition', '(unset)')}; "
            f"directive_bound={str(run.directive_bound).lower()}; "
            f"judge_active={str(run.judge_active).lower()}."
        )
        handoff = read_memory_handoff(run)
        if handoff:
            lines.append("")
            lines.append(handoff)
    if any(run.is_drafting for run in runs):
        lines.append(
            "DRAFTING runs: intake skill staged the manifest/scope-lock but the "
            "pipeline hasn't started. Scope guards are ADVISORY in this state — "
            "you'll see warnings but won't be auto-denied for scope violations. "
            "Resume the run with `/agent-pipeline-claude:run resume <run-id>` "
            "after the intake TODOs are filled in."
        )
    lines.append("Respect run.log, manifest.yaml, scope-lock.yaml, directive.yaml, and active-control-state.md before stopping or changing scope.")
    return "\n".join(lines)


def read_memory_handoff(run: ActiveRun) -> str:
    path = run.run_dir / "memory" / "handoff_current.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not text:
        return ""
    return "Agent Pipeline persistent memory:\n" + _truncate(text, 2400)


def stale_skill_context(prompt: str) -> str:
    lowered = prompt.lower()
    hits = []
    for skill in sorted(STALE_STANDALONE_SKILLS):
        if f"{NAMESPACED_PREFIX}{skill}" in lowered:
            continue
        if re.search(rf"(?<![\w:-]){re.escape(skill)}(?![\w:-])", lowered):
            hits.append(skill)
    if not hits:
        return ""
    replacements = ", ".join(f"{NAMESPACED_PREFIX}{name}" for name in hits)
    return f"Use namespaced Agent Pipeline skills to avoid stale standalone skills: {replacements}."


def prompt_bypass_context(prompt: str, runs: list[ActiveRun]) -> tuple[bool, str]:
    if not runs:
        return False, ""
    lowered = prompt.lower()
    bypass_terms = ("skip the gate", "bypass the gate", "ignore the manifest", "ignore scope-lock", "outside scope", "skip approval")
    if not any(term in lowered for term in bypass_terms):
        return False, ""
    return (
        True,
        "Active Agent Pipeline run detected. Do not bypass manifest, scope-lock, directive, judge, or human-gate protections; replan or ask for explicit operator authorization instead.",
    )


def tool_command(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
        return json.dumps(tool_input, sort_keys=True)
    if isinstance(tool_input, str):
        return tool_input
    return ""


# Tool names whose semantics are read-only on the local filesystem.
# These never trigger the "contract artifact touched" warning even if
# their tool_input mentions a contract filename (audit Pass 10 /
# Cluster I). Anything not in this set falls through to the existing
# write-class detection.
_READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset({
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "TodoWrite",  # writes to TodoList, not filesystem
})

# Bash subcommand tokens that are read-only on the local filesystem.
# A `bash` event whose first token (post-leading-newlines and after
# `cd … && `) matches one of these gets the same read-only treatment.
_READ_ONLY_BASH_TOKENS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more",
    "grep", "rg", "egrep", "fgrep",
    "ls", "dir", "tree",
    "wc", "find", "stat", "file",
    "git",  # git status / log / diff — write subcommands (commit, push) are caught by DESTRUCTIVE/EXTERNAL patterns
    "echo", "printf",
    # PowerShell readers
    "Get-Content", "Select-String", "Get-ChildItem", "Get-Item",
})


def _is_read_only_operation(event_or_command: Any) -> bool:
    """Return True for tool invocations that don't write to the local
    filesystem (audit Pass 10 / Cluster I).

    Used to suppress the "pipeline contract artifact touched" warning
    on Read-class tools and `cat manifest.yaml`-style Bash invocations.
    The pre-Pass-10 check warned on any tool_input containing
    `manifest.yaml`/`directive.yaml`/`scope-lock.yaml` regardless of
    intent — operators reading the file (which is the safe, encouraged
    behavior) saw the same noise as operators writing it.
    """
    if not isinstance(event_or_command, dict):
        return False
    tool_name = event_or_command.get("tool_name") or ""
    if isinstance(tool_name, str) and tool_name in _READ_ONLY_TOOL_NAMES:
        return True
    # Bash: inspect the first non-cd token.
    if tool_name == "Bash":
        cmd = tool_command(event_or_command).strip()
        if not cmd:
            return False
        # Strip leading `cd <dir> && ` chains (multi-step shell).
        while cmd.startswith("cd ") and "&&" in cmd:
            cmd = cmd.split("&&", 1)[1].strip()
        first_token = cmd.split(None, 1)[0] if cmd else ""
        # Heuristic guard: `git diff > out.txt` etc. is technically
        # write-class (redirect). Don't classify as read-only.
        if any(redir in cmd for redir in (">", ">>", "|tee", "| tee")):
            return False
        return first_token in _READ_ONLY_BASH_TOKENS
    return False


# --- v2.1.0: modal-budget enforcement -------------------------------------
#
# Background: v1.3.0 replaced chat-APPROVE gates with AskUserQuestion
# modal gates, on the premise that "one modal, one click, gate advances"
# eliminates the interpretive surface where the LLM either invented
# extra prompts or chickened out. That worked for the framework's OWN
# gates. It did NOT stop the orchestrator from manufacturing modal
# AskUserQuestion prompts BETWEEN gates — turning what should be three
# clicks (manifest, plan, manager) into 15+ clicks per run.
#
# v2.1.0 enforces a strict modal budget: during an active non-drafting
# pipeline run, AskUserQuestion is permitted ONLY when the orchestrator
# is at one of the pipeline yaml's declared `gate: human_approval`
# stages. Anything else is the "extra prompts" failure mode v1.3.0
# was supposed to eliminate; this hook closes the loophole.
#
# Drafting runs (intake mid-flight) bypass this: the intake skill
# itself asks one question if information is missing, which is legitimate
# pre-promotion modal use.

_GATE_STAGE_NAMES_CACHE: dict[Path, frozenset[str]] = {}


def _pipeline_yaml_for_run(run: ActiveRun) -> Path | None:
    """Resolve the pipeline yaml the run was started against.

    Convention: project root = run.run_dir.parent.parent (i.e. up from
    `.agent-runs/<id>/` to project root). Pipeline yaml lives at
    `.pipelines/<type>.yaml`. Type is read from the manifest if
    available, falling back to `feature` (the default pipeline).
    """
    try:
        project_root = run.run_dir.parent.parent
        manifest_path = run.run_dir / "manifest.yaml"
        pipeline_type = "feature"
        if manifest_path.exists():
            for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("type:"):
                    _, _, value = stripped.partition(":")
                    candidate = value.strip().strip("\"'")
                    if candidate:
                        pipeline_type = candidate
                    break
        yaml_path = project_root / ".pipelines" / f"{pipeline_type}.yaml"
        if yaml_path.exists():
            return yaml_path
    except Exception:
        # Hook code must never raise. If we can't find the yaml,
        # caller treats the budget as un-enforceable (allow).
        return None
    return None


def _gate_stages_from_yaml(yaml_path: Path) -> frozenset[str]:
    """Return the set of stage names declared as `gate: human_approval`."""
    cached = _GATE_STAGE_NAMES_CACHE.get(yaml_path)
    if cached is not None:
        return cached
    gate_stages: set[str] = set()
    try:
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
        current_name: str | None = None
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if stripped.startswith("- name:"):
                _, _, value = stripped.partition(":")
                current_name = value.strip().strip("\"'") or None
            elif stripped.startswith("gate:"):
                _, _, value = stripped.partition(":")
                if value.strip().strip("\"'") == "human_approval" and current_name:
                    gate_stages.add(current_name)
    except Exception:
        pass
    frozen = frozenset(gate_stages)
    _GATE_STAGE_NAMES_CACHE[yaml_path] = frozen
    return frozen


def modal_budget_decision(
    event: dict[str, Any], runs: list[ActiveRun]
) -> dict[str, Any] | None:
    """Return a deny payload if the AskUserQuestion call violates the budget.

    Permits the modal when:
      - no active non-drafting run exists (operator ad-hoc use is fine)
      - run is mid-intake (drafting bridge state)
      - current_stage matches a declared `gate: human_approval` stage in
        the pipeline yaml
      - the pipeline yaml can't be resolved (fail open, never on uncertainty)

    Denies otherwise with a structured reason naming the budget rule and
    pointing at the adopt-and-proceed alternative.
    """
    tool_name = event.get("tool_name") or ""
    if tool_name != "AskUserQuestion":
        return None
    # No active run, or all runs are drafting: bypass budget.
    non_drafting = [r for r in runs if not r.is_drafting]
    if not non_drafting:
        return None
    # Use the first active run as the budget authority (matches the
    # rest of the codebase's "primary run" convention).
    run = non_drafting[0]
    yaml_path = _pipeline_yaml_for_run(run)
    if yaml_path is None:
        return None  # fail open when we can't resolve the pipeline
    gate_stages = _gate_stages_from_yaml(yaml_path)
    if not gate_stages:
        return None  # pipeline declares no human gates, nothing to enforce
    current_stage = (run.fields.get("current_stage") or "").strip()
    # Accept current_stage matches OR last_completed_gate matches OR
    # current_stage is a "_done"/"_drafted" suffix of a gate stage,
    # which is the orchestrator's bookkeeping convention when a gate
    # has just fired and the run state hasn't advanced yet.
    if current_stage in gate_stages:
        return None
    last_completed = (run.fields.get("last_completed_gate") or "").strip()
    if last_completed and last_completed in gate_stages and current_stage in {"", "(unknown)"}:
        return None
    sorted_gates = sorted(gate_stages)
    reason = (
        "MODAL_BUDGET_EXCEEDED: this run's pipeline declares modal gates "
        f"only at stages {sorted_gates}; current_stage='{current_stage}' is "
        "not one of them. v2.1.0 hook enforces the v1.3.0 design that "
        "extra inter-gate modals are the failure mode AskUserQuestion gates "
        "were designed to eliminate. Re-evaluate: (a) if you have a "
        "researcher recommendation, ADOPT it, record in "
        "director-decisions.md, narrate in chat, proceed; (b) if the "
        "decision is genuinely outside scope (forbidden zone, irreversible "
        "without prior authorization), use BLOCK or REPLAN at the next "
        "mandated gate instead of inserting a modal here; (c) if this "
        "modal IS the mandated gate, update active-control-state.md "
        "current_stage to the gate-stage name before firing."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def classify_tool_risk(event: dict[str, Any], runs: list[ActiveRun]) -> tuple[str, list[str]]:
    command = tool_command(event)
    haystack = command.lower()
    reasons: list[str] = []
    severity = "allow"
    if _matches_any(command, DESTRUCTIVE_PATTERNS):
        severity = "deny"
        reasons.append("destructive or irreversible command pattern")
    if _matches_any(command, SECRET_PATTERNS):
        severity = "deny"
        reasons.append("credential or secret exposure pattern")
    if _matches_any(command, EXTERNAL_OR_RELEASE_PATTERNS):
        if severity != "deny":
            severity = "warn"
        reasons.append("external-facing release, network, or push operation")
    if _matches_any(command, DEPENDENCY_PATTERNS):
        if severity != "deny":
            severity = "warn"
        reasons.append("dependency installation changes project state")
    if runs and _touches_outside_allowed_paths(event, runs[0].run_dir):
        severity = "deny"
        reasons.append("write target appears outside manifest allowed_paths during an active run")
    # Audit Pass 10 / Cluster I: only warn on contract-artifact touches
    # for WRITE-class operations. Reads (cat, grep, Read tool) are the
    # safe, encouraged behavior and shouldn't get the same warning as
    # someone editing the manifest mid-run.
    if (
        ("directive.yaml" in haystack or "manifest.yaml" in haystack or "scope-lock.yaml" in haystack)
        and not _is_read_only_operation(event)
    ):
        if severity != "deny":
            severity = "warn"
        reasons.append("pipeline contract artifact touched")
    return severity, reasons


def permission_decision(event: dict[str, Any], runs: list[ActiveRun]) -> dict[str, Any] | None:
    severity, reasons = classify_tool_risk(event, runs)
    # Pass 12 / Cluster K: when every active run is in drafting state
    # (intake-staged, pipeline not yet started), the scope guards are
    # advisory — we still surface reasons to the operator via the
    # session context, but we do NOT auto-deny on scope violations
    # alone. Destructive / secret-exposure patterns still deny because
    # those reasons are absolute, not run-scoped.
    only_drafting = bool(runs) and all(run.is_drafting for run in runs)
    if severity == "deny" and only_drafting:
        # Drop the run-scoped deny reasons; keep the absolute ones.
        absolute = [r for r in reasons if not _is_run_scoped_reason(r)]
        if not absolute:
            return None  # all reasons were scope-bound → no deny while drafting
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": (
                        "Agent Pipeline hook denied approval request "
                        "(drafting run; scope guards advisory, but absolute "
                        "policies still apply): " + "; ".join(absolute)
                    ),
                },
            }
        }
    if severity == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": "Agent Pipeline hook denied approval request: " + "; ".join(reasons),
                },
            }
        }
    if severity == "allow" and runs and runs[0].directive_bound and not runs[0].is_drafting:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    return None


_RUN_SCOPED_REASON_SUBSTRINGS: tuple[str, ...] = (
    "write target appears outside manifest allowed_paths",
    "pipeline contract artifact touched",
)


def _is_run_scoped_reason(reason: str) -> bool:
    """Return True for classify_tool_risk reasons that are bound to the
    active run's manifest/scope (Pass 12). Drafting runs downgrade these
    from deny to advisory because the manifest/scope itself is mid-
    draft. Absolute reasons (destructive command, credential exposure)
    are NOT in this list — they apply regardless of run state."""
    lowered = reason.lower()
    return any(needle in lowered for needle in _RUN_SCOPED_REASON_SUBSTRINGS)


def tool_failure_context(event: dict[str, Any]) -> str:
    response = event.get("tool_response")
    pieces: list[str] = []
    failed = _tool_response_failed(response)
    if failed:
        pieces.append("The last tool result appears to contain a failure. Inspect the command output, fix the root cause, and rerun the relevant verification before advancing the pipeline.")
    command = tool_command(event).lower()
    # Pass 10 / Cluster I: same read-vs-write distinction as
    # classify_tool_risk — a successful `cat manifest.yaml` does not
    # need the "re-run policy checks" prompt; only a write does.
    if (
        any(name in command for name in ("manifest.yaml", "scope-lock.yaml", "directive.yaml"))
        and not _is_read_only_operation(event)
    ):
        pieces.append("A pipeline contract artifact was touched. Re-run directive/scope/manifest policy checks before relying on any auto-approval.")
    if "pytest" in command and failed:
        pieces.append("Tests failed. Do not mark the stage complete until pytest is green or the failing gate records a valid human stop condition.")
    return "\n".join(pieces)


def _tool_response_failed(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    for name in ("exit_code", "exitCode", "returncode", "return_code", "status"):
        if name not in response:
            continue
        value = response.get(name)
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized.isdigit():
                return int(normalized) != 0
            if normalized in {"failed", "failure", "error"}:
                return True
            if normalized in {"ok", "success", "passed", "pass"}:
                return False
    stderr = str(response.get("stderr") or "")
    return bool(stderr.strip() and any(marker in stderr.lower() for marker in ("traceback", "error:", "exception")))


def stop_continuation(repo_root: Path) -> str:
    plugin_root = Path(__file__).resolve().parents[1]
    for import_root in (repo_root, plugin_root):
        root_text = str(import_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    from scripts.final_response_gate import evaluate_final_response_gate

    results = evaluate_final_response_gate(repo_root / ".agent-runs", require_active_run=False)
    blocked = [result for result in results if not result.allowed]
    if not blocked:
        return ""
    lines = ["Agent Pipeline run is not at a valid stop condition. Continue the run before sending a final response."]
    for result in blocked:
        lines.append(f"- {result.reason}")
        if result.continuing_to:
            lines.append(f"  continuing_to: {result.continuing_to}")
        if result.next_required_action:
            lines.append(f"  next_required_action: {result.next_required_action}")
    return "\n".join(lines)


def append_hook_event(repo_root: Path, event_name: str, message: str) -> None:
    runs = discover_active_runs(repo_root)
    if not runs:
        return
    path = runs[0].run_dir / "hook-events.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event_name,
        "message": message,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# Map hook event names -> default PRD FR-7 taxonomy type. Without this,
# every Layer A record was written with `metadata = {}` and the Layer
# A→B flush filter (which requires metadata.type in allowed_types)
# silently dropped 100% of them as `skipped_no_type` (audit Pass 9 /
# QA-001). Callers can still override by passing an explicit
# `metadata={"type": "..."}`.
#
# Categories chosen for default-most-useful retrieval semantics:
#   - PostToolUseFailure → `anti_pattern` so it surfaces under
#     "what failed last time."
#   - UserPromptSubmit → `session_state` to preserve dialog continuity
#     across compactions (PostCompact re-injects handoff_current.md).
#   - All other lifecycle events → `session_state`.
# Explicit metadata.type from the caller (open-loops, decisions) wins.
_EVENT_DEFAULT_TYPE: dict[str, str] = {
    "SessionStart": "session_state",
    "UserPromptSubmit": "session_state",
    "PreToolUse": "session_state",
    "PermissionRequest": "session_state",
    "PostToolUse": "session_state",
    "PostToolUseFailure": "anti_pattern",
    "PreCompact": "session_state",
    "PostCompact": "session_state",
    "SubagentStop": "session_state",
    "Stop": "session_state",
    "SessionEnd": "session_state",
}


def _redact_message_for_layer_a(message: str) -> tuple[str, bool, list[str]]:
    """Pre-write redaction (audit Pass 9 / ENG-008). Returns
    (sanitized_message, was_redacted, matched_patterns).

    Layer A writes happen unconditionally — they're the durable floor
    that survives Layer B (Mem0) outages. Before the fix, Bash commands
    with embedded secrets (e.g. ``curl -H "Authorization: Bearer …"``)
    were written verbatim to ``.agent-runs/<run-id>/memory/*.jsonl``.
    Now we run ``scrub()`` against the canonical pattern list; when a
    secret is detected the record is preserved (timestamp + event +
    run_id still useful for traceability) but the message body is
    replaced with a sentinel and the matched-pattern count goes into
    ``metadata.redacted``.

    The redaction is fail-closed: if ``scrub()`` raises (malformed
    regex), the message is treated as secret-bearing and redacted.
    """
    if not message:
        return message, False, []
    try:
        # Import locally to keep hooks importable when the memory
        # package isn't on PYTHONPATH (e.g. minimal test contexts).
        from memory.redaction import scrub
    except ImportError:
        return message, False, []
    try:
        result = scrub(message)
    except Exception:  # noqa: BLE001 — fail-closed
        return "[REDACTED: scrub raised; treating as secret]", True, ["<scrub-error>"]
    if result.allowed:
        return message, False, []
    return (
        f"[REDACTED: {result.reason}]",
        True,
        list(result.matched_patterns) + list(result.matched_paths),
    )


def record_hook_memory(repo_root: Path, event_name: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    runs = discover_active_runs(repo_root)
    if not runs:
        return
    run = runs[0]
    memory_dir = run.run_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Pass 9 / ENG-008: pre-write scrub against the canonical secret
    # patterns so Bash commands with embedded credentials never reach
    # disk verbatim. Preserves the event row for traceability but
    # replaces the message body with a redaction sentinel.
    sanitized, was_redacted, matched = _redact_message_for_layer_a(message)
    truncated = _truncate(sanitized, MAX_MEMORY_TEXT)

    # Pass 9 / QA-001: auto-populate metadata.type from the event name
    # so the Layer A→B flush filter (which requires metadata.type in
    # allowed_types) actually sees these records as candidates instead
    # of silently dropping them as skipped_no_type. Caller-supplied
    # `metadata["type"]` wins so callers like the decision-ledger or
    # intake skill can override (e.g. "decision", "task_learning").
    merged_metadata: dict[str, Any] = dict(metadata or {})
    if not merged_metadata.get("type"):
        default_type = _EVENT_DEFAULT_TYPE.get(event_name)
        if default_type:
            merged_metadata["type"] = default_type
    if was_redacted:
        merged_metadata["redacted"] = True
        merged_metadata["redacted_match_count"] = len(matched)

    record = {
        "timestamp": _utc_now(),
        "event": event_name,
        "run_id": run.run_id,
        "stage": run.fields.get("current_stage", ""),
        "message": truncated,
        "metadata": merged_metadata,
    }
    target_file = memory_dir / _memory_file_for_event(event_name)
    append_jsonl(target_file, record)
    if target_file.name != "events.jsonl":
        append_jsonl(memory_dir / "events.jsonl", record)
    _write_memory_probe(memory_dir, repo_root, event_name, run)
    _write_handoff(run, memory_dir)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _write_handoff(run: ActiveRun, memory_dir: Path) -> None:
    event_rows = _read_jsonl_tail(memory_dir / "events.jsonl", MAX_HANDOFF_RECORDS)
    open_loop_rows = _read_jsonl_tail(memory_dir / "open_loops.jsonl", MAX_HANDOFF_RECORDS)
    decision_rows = _read_jsonl_tail(memory_dir / "decisions.jsonl", MAX_HANDOFF_RECORDS)
    lines = [
        f"# Agent Pipeline memory - {run.run_id}",
        "",
        f"Generated: {_utc_now()}",
        "",
        "## Run State",
        "",
        f"- stage: {run.fields.get('current_stage', '(unknown)')}",
        f"- next_required_action: {run.fields.get('next_required_action', '(unspecified)')}",
        f"- continuing_to: {run.fields.get('continuing_to', '(unspecified)')}",
        f"- stop_condition: {run.fields.get('stop_condition', '(unset)')}",
        f"- directive_bound: {str(run.directive_bound).lower()}",
        f"- judge_active: {str(run.judge_active).lower()}",
        "",
    ]
    if open_loop_rows:
        lines.extend(["## Open Loops", ""])
        for row in open_loop_rows:
            lines.append(f"- [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    if decision_rows:
        lines.extend(["## Recent Decisions And Warnings", ""])
        for row in decision_rows:
            lines.append(f"- [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    if event_rows:
        lines.extend(["## Recent Hook Memory", ""])
        for row in event_rows:
            lines.append(f"- {row.get('timestamp', '')} [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    lines.extend(
        [
            "## Resume Checklist",
            "",
            "- Read the run contract files and memory/*.jsonl before changing scope.",
            "- Re-run relevant policy checks before relying on any remembered approval, warning, or failure state.",
        ]
    )
    (memory_dir / "handoff_current.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_memory_probe(memory_dir: Path, repo_root: Path, event_name: str, run: ActiveRun) -> None:
    with (memory_dir / "memory_probe.log").open("a", encoding="utf-8") as handle:
        handle.write(f"[{_utc_now()}] event={event_name} repo={repo_root} run={run.run_id} stage={run.fields.get('current_stage', '')}\n")


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows[-limit:]


def _memory_file_for_event(event_name: str) -> str:
    if event_name == "UserPromptSubmit":
        return "turns.jsonl"
    if event_name in {"PreToolUse", "PermissionRequest"}:
        return "decisions.jsonl"
    if event_name in {"PostToolUse", "PostToolUseFailure", "Stop"}:
        return "open_loops.jsonl"
    # PreCompact, PostCompact, SubagentStop, SessionEnd, SessionStart all
    # land in events.jsonl. They are bookkeeping rather than decisions or
    # turns, and the handoff pulls from events.jsonl as the catch-all tail.
    return "events.jsonl"


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...[truncated]"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _directive_bound(run_dir: Path) -> bool:
    log = run_dir / "run.log"
    if not log.exists():
        return False
    return "directive-bound | COMPLETE | hash=" in log.read_text(encoding="utf-8-sig", errors="replace")


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def _touches_outside_allowed_paths(event_or_command, run_dir: Path) -> bool:
    """Check if a tool would write to a path outside manifest.allowed_paths.

    Accepts either a Cowork event dict (preferred — extracts file_path from
    structured tool_input for Write/Edit/MultiEdit/NotebookEdit) or a bare
    command string (legacy callers that already serialized to text).
    Returns True only when ALL extracted candidate paths are outside the
    allowed set and the allowed set is non-empty.
    """
    manifest = run_dir / "manifest.yaml"
    if not manifest.exists():
        return False
    allowed = _manifest_list(manifest, "allowed_paths")
    if not allowed:
        return False

    candidates = _extract_write_paths(event_or_command)
    if not candidates:
        return False

    for raw in candidates:
        normalized = raw.replace("\\", "/").lstrip("./")
        if not any(
            normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/")
            for item in allowed
        ):
            return True
    return False


# Explicit allowlist of MCP write tools and the tool_input fields where
# they carry their target file path. Generic recursive path extraction
# was rejected during audit synthesis (Pass 7 / Cluster G) because it
# false-positives on every MCP that happens to have a string field
# named "path" or "destination" — including remote APIs (mcp__github__*,
# mcp__slack__*, ...) that never touch the local filesystem. The
# audit-locked decision: explicit allowlist of LOCAL-filesystem write
# tools only.
#
# Each entry maps a compiled regex matching the tool_name to a tuple of
# tool_input field names that hold local file paths. Add new entries
# here as new local-filesystem MCPs are adopted by operators.
#
# Intentionally NOT in the allowlist:
#   * `mcp__github__create_or_update_file`, `mcp__github__push_files` —
#     push to GitHub via API, do NOT modify the local working tree.
#     Remote pushes are gated by EXTERNAL_OR_RELEASE_PATTERNS, not by
#     scope-lock allowed_paths.
#   * `mcp__*__send_message`, `mcp__*__post_*` — outbound network calls,
#     no local write surface.
MCP_LOCAL_WRITE_TOOL_RULES: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"^mcp__.+__create_file$"), ("path", "file_path")),
    (re.compile(r"^mcp__.+__copy_file$"), ("destination", "destination_path", "to", "target")),
    (re.compile(r"^mcp__.+__write_file$"), ("path", "file_path")),
    (re.compile(r"^mcp__.+__upload_file$"), ("path", "file_path", "destination")),
    (re.compile(r"^mcp__.+__save_profile$"), ("path", "profile_path")),
    # PDF tools: fill_pdf, merge_pdfs, reorder_pdf_pages, etc. produce
    # output files locally per tool_input.output (or output_path).
    (re.compile(r"^mcp__.+__fill_pdf$"), ("output", "output_path")),
    (re.compile(r"^mcp__.+__merge_pdfs$"), ("output", "output_path")),
    (re.compile(r"^mcp__.+__split_pdf$"), ("output", "output_path", "output_dir")),
    (re.compile(r"^mcp__.+__bulk_fill_from_csv$"), ("output", "output_path", "output_dir")),
)


def _extract_mcp_local_write_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Apply the explicit MCP-local-write allowlist to a tool_input dict.

    Returns every field-value from the allowlist's per-tool field list
    that looks like a non-empty string path. Unknown MCP tools return
    [] — those go through the rest of `_extract_write_paths`'s
    extraction path (which finds nothing for MCPs that aren't in the
    allowlist, by design).
    """
    if not tool_name or not isinstance(tool_input, dict):
        return []
    for pattern, fields in MCP_LOCAL_WRITE_TOOL_RULES:
        if pattern.match(tool_name):
            paths: list[str] = []
            for field_name in fields:
                value = tool_input.get(field_name)
                if isinstance(value, str) and value:
                    paths.append(value)
            return paths
    return []


def _extract_write_paths(event_or_command) -> list[str]:
    """Return every file path a tool call would write to.

    For Cowork event dicts: pulls `tool_input.file_path` (Write / Edit /
    NotebookEdit), `tool_input.edits[].file_path` (MultiEdit), and falls
    back to shell-command parsing for Bash. Also consults the explicit
    MCP allowlist at ``MCP_LOCAL_WRITE_TOOL_RULES`` for local-filesystem
    write MCPs (Pass 7 / Cluster G). For bare strings: only the
    shell-command path.
    """
    paths: list[str] = []
    if isinstance(event_or_command, dict):
        tool_input = event_or_command.get("tool_input")
        if isinstance(tool_input, dict):
            file_path = tool_input.get("file_path")
            if isinstance(file_path, str) and file_path:
                paths.append(file_path)
            # MultiEdit: edits list with per-entry file_path
            edits = tool_input.get("edits")
            if isinstance(edits, list):
                for edit in edits:
                    if isinstance(edit, dict):
                        fp = edit.get("file_path")
                        if isinstance(fp, str) and fp:
                            paths.append(fp)
            # NotebookEdit may use notebook_path
            nb_path = tool_input.get("notebook_path")
            if isinstance(nb_path, str) and nb_path:
                paths.append(nb_path)
            # Allowlisted MCP local-write tools (Pass 7 / Cluster G).
            tool_name = event_or_command.get("tool_name") or ""
            if isinstance(tool_name, str):
                paths.extend(_extract_mcp_local_write_paths(tool_name, tool_input))
        # Always also try the shell command if present
        command_text = tool_command(event_or_command)
    else:
        command_text = str(event_or_command or "")

    legacy = _extract_write_path(command_text)
    if legacy:
        paths.append(legacy)
    # de-dupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _manifest_list(path: Path, key: str) -> list[str]:
    """Collect `- ...` list items for a YAML key, terminating cleanly at the
    next sibling key.

    Earlier implementation walked until an unindented line, which spilled
    across sibling keys in indented YAML (e.g. allowed_paths sitting under
    pipeline_run: would absorb required_gates items). This version tracks
    the indent of the matched key and terminates as soon as a non-list line
    appears at or shallower than that indent.
    """
    values: list[str] = []
    in_key = False
    key_indent = -1
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = len(raw) - len(raw.lstrip(" \t"))
        if not in_key:
            if stripped.startswith(f"{key}:"):
                in_key = True
                key_indent = line_indent
            continue
        # In list-collection mode for `key`
        if stripped.startswith("- "):
            # Require strictly deeper indent than the key itself
            if line_indent > key_indent:
                values.append(stripped[2:].strip().strip("\"'"))
            else:
                # A dash at <= key_indent means we left this key's subtree
                break
            continue
        # Any other content terminates if it is at or shallower than the key indent
        if line_indent <= key_indent:
            break
    return values


def _extract_write_path(command: str) -> str:
    match = re.search(r"(?:Set-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item)\s+(?:-LiteralPath\s+|-Path\s+)?['\"]?([^'\"\s]+)", command, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"(?:>|>>)\s*['\"]?([^'\"\s]+)", command)
    if match:
        return match.group(1)
    return ""
