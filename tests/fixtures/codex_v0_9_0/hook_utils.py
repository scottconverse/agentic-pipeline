#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Agent Pipeline Codex lifecycle hooks."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STALE_STANDALONE_SKILLS = {
    "agent-pipeline",
    "audit-init",
    "new-run",
    "pipeline-init",
    "run-pipeline",
    "show-run-status",
    "validate-manifest",
}
NAMESPACED_PREFIX = "agent-pipeline-codex:"
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
    cwd = event.get("cwd") or os.getcwd()
    path = Path(str(cwd)).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".agent-runs").exists() or (candidate / ".codex-plugin").exists() or (candidate / ".git").exists():
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
    base = repo_root / ".agent-runs"
    if not base.exists():
        return []
    runs: list[ActiveRun] = []
    for state_path in sorted(base.glob("*/active-control-state.md")):
        fields = parse_control_state(state_path.read_text(encoding="utf-8-sig", errors="replace"))
        if fields.get("active_run", "").lower() != "true":
            continue
        run_dir = state_path.parent
        runs.append(
            ActiveRun(
                run_id=run_dir.name,
                run_dir=run_dir,
                state_path=state_path,
                fields=fields,
                directive_bound=_directive_bound(run_dir),
                judge_active=(repo_root / ".pipelines" / "action-classification.yaml").exists(),
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
        lines.append(
            "- "
            f"run={run.run_id}; stage={run.fields.get('current_stage', '(unknown)')}; "
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
    if runs and _touches_outside_allowed_paths(command, runs[0].run_dir):
        severity = "deny"
        reasons.append("write target appears outside manifest allowed_paths during an active run")
    if "directive.yaml" in haystack or "manifest.yaml" in haystack or "scope-lock.yaml" in haystack:
        if severity != "deny":
            severity = "warn"
        reasons.append("pipeline contract artifact touched")
    return severity, reasons


def permission_decision(event: dict[str, Any], runs: list[ActiveRun]) -> dict[str, Any] | None:
    severity, reasons = classify_tool_risk(event, runs)
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
    if severity == "allow" and runs and runs[0].directive_bound:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    return None


def tool_failure_context(event: dict[str, Any]) -> str:
    response = event.get("tool_response")
    pieces: list[str] = []
    failed = _tool_response_failed(response)
    if failed:
        pieces.append("The last tool result appears to contain a failure. Inspect the command output, fix the root cause, and rerun the relevant verification before advancing the pipeline.")
    command = tool_command(event).lower()
    if any(name in command for name in ("manifest.yaml", "scope-lock.yaml", "directive.yaml")):
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


def record_hook_memory(repo_root: Path, event_name: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    runs = discover_active_runs(repo_root)
    if not runs:
        return
    run = runs[0]
    memory_dir = run.run_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": _utc_now(),
        "event": event_name,
        "run_id": run.run_id,
        "stage": run.fields.get("current_stage", ""),
        "message": _truncate(message, MAX_MEMORY_TEXT),
        "metadata": metadata or {},
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
    if event_name in {"PostToolUse", "Stop"}:
        return "open_loops.jsonl"
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


def _touches_outside_allowed_paths(command: str, run_dir: Path) -> bool:
    manifest = run_dir / "manifest.yaml"
    if not manifest.exists():
        return False
    allowed = _manifest_list(manifest, "allowed_paths")
    if not allowed:
        return False
    candidate = _extract_write_path(command)
    if not candidate:
        return False
    normalized = candidate.replace("\\", "/").lstrip("./")
    return not any(normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/") for item in allowed)


def _manifest_list(path: Path, key: str) -> list[str]:
    values: list[str] = []
    in_key = False
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = raw.strip()
        if stripped.startswith(f"{key}:"):
            in_key = True
            continue
        if in_key and stripped.startswith("- "):
            values.append(stripped[2:].strip().strip("\"'"))
            continue
        if in_key and stripped and not raw.startswith((" ", "\t")):
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
