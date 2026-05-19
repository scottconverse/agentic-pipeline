#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Claude Code lifecycle hook entrypoint for Agent Pipeline.

Ported from agent-pipeline-codex v0.9.0 (hooks/hook_runner.py). Extended
for claude's heavier-hand approach with 5 additional Cowork hook events:
PostToolUseFailure, PreCompact, PostCompact, SubagentStop, SessionEnd.

The 11 total events bracket the run lifecycle:
- SessionStart        - inject active run context + memory handoff
- UserPromptSubmit    - warn on stale skill names, block bypass attempts
- PreToolUse          - classify tool risk; deny destructive / out-of-scope
- PermissionRequest   - auto-deny dangerous; auto-allow when directive-bound
- PostToolUse         - corrective context after failed tools
- PostToolUseFailure  - dedicated failure recording with severity
- PreCompact          - snapshot memory before context compaction
- PostCompact         - re-inject handoff memory after compaction
- SubagentStop        - record subagent completion to memory
- Stop                - block invalid pipeline stops
- SessionEnd          - final memory flush; safety net for Mem0 (Phase 5)
"""

from __future__ import annotations

import sys

try:
    from hook_utils import (
        _tool_response_failed,
        append_hook_event,
        classify_tool_risk,
        discover_active_runs,
        modal_budget_decision,
        policy_recheck_decision,
        pop_pending_recheck_on_bash_success,
        record_pending_recheck_for_write,
        stage_artifact_format_decision,
        permission_decision,
        prompt_bypass_context,
        read_hook_input,
        read_memory_handoff,
        record_hook_memory,
        repo_root_from_event,
        session_context,
        stale_skill_context,
        stop_continuation,
        tool_failure_context,
        write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - package import from tests
    from hooks.hook_utils import (
        _tool_response_failed,
        append_hook_event,
        classify_tool_risk,
        discover_active_runs,
        modal_budget_decision,
        policy_recheck_decision,
        pop_pending_recheck_on_bash_success,
        record_pending_recheck_for_write,
        stage_artifact_format_decision,
        permission_decision,
        prompt_bypass_context,
        read_hook_input,
        read_memory_handoff,
        record_hook_memory,
        repo_root_from_event,
        session_context,
        stale_skill_context,
        stop_continuation,
        tool_failure_context,
        write_json,
    )


def _context_payload(event_name: str, context: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


def handle_session_start(event: dict) -> int:
    if event.get("source") not in {"startup", "resume", "compact", "clear"}:
        return 0
    root = repo_root_from_event(event)
    context = session_context(discover_active_runs(root))
    if not context:
        return 0
    append_hook_event(root, "SessionStart", "added active run context")
    record_hook_memory(root, "SessionStart", "loaded active run context")
    return write_json(_context_payload("SessionStart", context))


def handle_user_prompt_submit(event: dict) -> int:
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    prompt = str(event.get("prompt") or "")
    contexts = [item for item in (stale_skill_context(prompt),) if item]
    blocked, bypass = prompt_bypass_context(prompt, runs)
    if blocked:
        append_hook_event(root, "UserPromptSubmit", "blocked pipeline bypass prompt")
        record_hook_memory(root, "UserPromptSubmit", bypass, {"blocked": True})
        return write_json({"decision": "block", "reason": bypass})
    if not contexts:
        if runs and prompt:
            record_hook_memory(root, "UserPromptSubmit", prompt, {"blocked": False})
        return 0
    context = "\n".join(contexts)
    append_hook_event(root, "UserPromptSubmit", context)
    record_hook_memory(root, "UserPromptSubmit", context, {"blocked": False})
    return write_json(_context_payload("UserPromptSubmit", context))


def handle_pre_tool_use(event: dict) -> int:
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    # v2.2.0 hook-acknowledgement enforcement — check BEFORE all other
    # PreToolUse decision functions. If a prior write touched a contract
    # artifact and the operator hasn't run the required policy recheck
    # yet, deny non-recheck operations. Closes the v2.0.x "noted,
    # continuing" failure mode where contract-artifact-touched warnings
    # were acknowledged conversationally and immediately ignored.
    recheck_decision = policy_recheck_decision(event, runs)
    if recheck_decision is not None:
        reason = recheck_decision.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", "policy recheck required"
        )
        append_hook_event(root, "PreToolUse", "policy-recheck deny: " + reason[:200])
        record_hook_memory(
            root, "PreToolUse", reason[:400], {"severity": "deny", "rule": "policy_recheck"}
        )
        return write_json(recheck_decision)
    # v2.1.0 modal-budget enforcement — check BEFORE classify_tool_risk
    # because the AskUserQuestion path is a structural concern (where in
    # the pipeline are we?) not a content-risk concern (what command?).
    modal_decision = modal_budget_decision(event, runs)
    if modal_decision is not None:
        reason = modal_decision.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", "modal budget exceeded"
        )
        append_hook_event(root, "PreToolUse", "modal-budget deny: " + reason[:200])
        record_hook_memory(
            root, "PreToolUse", reason[:400], {"severity": "deny", "rule": "modal_budget"}
        )
        return write_json(modal_decision)
    # v2.1.0 stage-artifact format conformance — check BEFORE the generic
    # risk classifier so we get a focused error message about the
    # specific marker requirement, not a generic "contract artifact
    # touched" warning.
    artifact_decision = stage_artifact_format_decision(event, runs)
    if artifact_decision is not None:
        reason = artifact_decision.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", "stage artifact format violation"
        )
        append_hook_event(root, "PreToolUse", "stage-artifact-format deny: " + reason[:200])
        record_hook_memory(
            root,
            "PreToolUse",
            reason[:400],
            {"severity": "deny", "rule": "stage_artifact_format"},
        )
        return write_json(artifact_decision)
    severity, reasons = classify_tool_risk(event, runs)
    if severity == "deny":
        reason = "Agent Pipeline hook denied tool call: " + "; ".join(reasons)
        append_hook_event(root, "PreToolUse", reason)
        record_hook_memory(root, "PreToolUse", reason, {"severity": "deny"})
        return write_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    if severity == "warn":
        context = "Agent Pipeline hook warning: " + "; ".join(reasons) + ". Confirm manifest, scope-lock, directive, and judge policy before treating this action as authorized."
        append_hook_event(root, "PreToolUse", context)
        record_hook_memory(root, "PreToolUse", context, {"severity": "warn"})
        return write_json(_context_payload("PreToolUse", context))
    return 0


def handle_permission_request(event: dict) -> int:
    root = repo_root_from_event(event)
    decision = permission_decision(event, discover_active_runs(root))
    if decision is None:
        return 0
    append_hook_event(root, "PermissionRequest", "returned approval decision")
    record_hook_memory(root, "PermissionRequest", "returned approval decision", decision.get("hookSpecificOutput", {}))
    return write_json(decision)


def handle_post_tool_use(event: dict) -> int:
    """Surface corrective context after tool calls.

    Behavior split (fix from Phase 6.b verification report):
      * On actual tool failure  -> decision: block + additionalContext.
        Cowork surfaces this as a blocking error, which is correct: the
        run state is genuinely broken.
      * On successful contract-artifact touch -> additionalContext only,
        no decision: block. Earlier behavior returned block here too,
        which made every successful write to manifest/scope-lock/directive
        render as a red error in Cowork even though the write succeeded.

    v2.2.0 extensions (hook-acknowledgement enforcement):
      * On successful Write/Edit/MultiEdit/NotebookEdit to a contract
        artifact -> append the required policy recheck command to the
        run's ``pending-policy-recheck.txt`` sidecar.
      * On successful Bash that matches a pending recheck command ->
        pop that line from the sidecar.
      Both side-effects are best-effort: failures to update the sidecar
      do not block the tool result. The next PreToolUse will re-evaluate
      from the persisted sidecar state.
    """
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    failed = _tool_response_failed(event.get("tool_response"))
    # v2.2.0 sidecar updates fire only on success. The PreToolUse-side
    # enforcement reads the sidecar on the next call, so these
    # post-write/post-bash side-effects are what feed the deny path.
    if not failed:
        appended = record_pending_recheck_for_write(event, runs)
        if appended:
            append_hook_event(root, "PostToolUse", "policy-recheck pending: " + appended[:200])
            record_hook_memory(
                root, "PostToolUse", "policy-recheck pending: " + appended[:300],
                {"severity": "info", "rule": "policy_recheck_pending"},
            )
        popped = pop_pending_recheck_on_bash_success(event, runs)
        if popped:
            append_hook_event(root, "PostToolUse", "policy-recheck cleared: " + popped[:200])
            record_hook_memory(
                root, "PostToolUse", "policy-recheck cleared: " + popped[:300],
                {"severity": "info", "rule": "policy_recheck_cleared"},
            )
    context = tool_failure_context(event)
    if not context:
        return 0
    append_hook_event(root, "PostToolUse", context)
    record_hook_memory(root, "PostToolUse", context, {"blocked": failed})
    payload: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }
    if failed:
        payload["decision"] = "block"
        payload["reason"] = "Agent Pipeline hook is replacing the tool result with pipeline continuation guidance."
    return write_json(payload)


def handle_post_tool_use_failure(event: dict) -> int:
    """Dedicated handler for Cowork's PostToolUseFailure event.

    Unlike PostToolUse (which fires on success), this event fires when the
    tool itself failed. Record the failure to open_loops.jsonl with high
    severity and surface corrective context. Does not block - Cowork already
    knows the tool failed; this just makes sure the failure becomes part of
    the run's durable memory rather than being lost when the context window
    rolls over.
    """
    root = repo_root_from_event(event)
    context = tool_failure_context(event) or (
        "A tool call failed. Inspect the output, fix the root cause, "
        "and rerun verification before advancing the pipeline."
    )
    append_hook_event(root, "PostToolUseFailure", context)
    record_hook_memory(root, "PostToolUseFailure", context, {"severity": "high", "blocked": False})
    return write_json(_context_payload("PostToolUseFailure", context))


def handle_pre_compact(event: dict) -> int:
    """Cowork compaction is imminent. Force a memory snapshot so the run's
    state is durable before the context window is rewritten. The
    record_hook_memory call regenerates handoff_current.md as a side effect.
    """
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    if not runs:
        return 0
    source = event.get("source", "unknown")
    message = f"Context compaction imminent (source={source}). Run state snapshot captured."
    append_hook_event(root, "PreCompact", message)
    record_hook_memory(root, "PreCompact", message, {"source": source})
    return 0


def handle_post_compact(event: dict) -> int:
    """Compaction complete. Re-inject handoff_current.md as additionalContext
    so Claude sees the pre-compaction run state on the other side of the
    rewrite. This is the most concrete value of the persistent-memory layer:
    compaction can no longer silently drop run state.
    """
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    if not runs:
        return 0
    handoff = read_memory_handoff(runs[0])
    if not handoff:
        return 0
    source = event.get("source", "unknown")
    append_hook_event(root, "PostCompact", f"re-injected memory handoff after compaction (source={source})")
    record_hook_memory(root, "PostCompact", "re-injected memory handoff after compaction", {"source": source})
    return write_json(_context_payload("PostCompact", handoff))


def handle_subagent_stop(event: dict) -> int:
    """Subagent finished. Record completion to memory so the parent pipeline
    has durable evidence of what each spawned subagent did, even after
    context compaction. The pipeline orchestrator spawns subagents
    constantly (research, planner, executor, verifier, critic, judge) -
    losing their outcomes between sessions has historically caused drift.
    """
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    if not runs:
        return 0
    agent_id = str(event.get("agent_id") or "unknown")
    agent_type = str(event.get("agent_type") or "unknown")
    message = f"Subagent {agent_type} ({agent_id}) finished."
    append_hook_event(root, "SubagentStop", message)
    record_hook_memory(root, "SubagentStop", message, {"agent_id": agent_id, "agent_type": agent_type})
    return 0


def handle_stop(event: dict) -> int:
    if event.get("stop_hook_active") is True:
        return 0
    root = repo_root_from_event(event)
    continuation = stop_continuation(root)
    if not continuation:
        return 0
    append_hook_event(root, "Stop", "continued invalid pipeline stop")
    record_hook_memory(root, "Stop", continuation, {"blocked": True})
    return write_json({"decision": "block", "reason": continuation})


def handle_session_end(event: dict) -> int:
    """Session terminating. Final memory flush + fire-and-forget Mem0 sync.

    Records the SessionEnd event to Layer A, then spawns
    `python scripts/mem0_bootstrap.py sync` as a detached background
    subprocess (no wait, no timeout in this hook handler) so Layer B
    catches up before the next session starts.

    Synchronous network I/O in a 30-second hook timeout is dangerous;
    a fire-and-forget subprocess is the cleaner pattern. The
    background process inherits the env so CLAUDE_PROJECT_DIR routes
    correctly. Stdout/stderr are dropped because the hook handler is
    closing anyway.
    """
    root = repo_root_from_event(event)
    runs = discover_active_runs(root)
    if not runs:
        return 0
    reason = str(event.get("reason") or "session_ended")
    message = f"Session ending (reason={reason}); final memory snapshot."
    append_hook_event(root, "SessionEnd", message)
    record_hook_memory(root, "SessionEnd", message, {"reason": reason, "final_flush": True})

    _spawn_mem0_sync_detached(root)
    return 0


def _spawn_mem0_sync_detached(repo_root) -> None:
    """Fire-and-forget background subprocess for `mem0 sync`.

    Best-effort: only runs when .mem0/config.json exists (Mem0 enabled).
    Silently no-ops otherwise. Errors are intentionally not surfaced
    because the SessionEnd hook is closing and operator visibility into
    background failures comes from the Layer A outbox + the next
    SessionStart's handoff_current.md.
    """
    import os
    import subprocess
    import sys

    config_path = repo_root / ".mem0" / "config.json"
    if not config_path.exists():
        return
    plugin_root = None
    try:
        from pathlib import Path
        plugin_root = Path(__file__).resolve().parents[1]
    except Exception:  # noqa: BLE001
        return
    if plugin_root is None:
        return
    bootstrap = plugin_root / "scripts" / "mem0_bootstrap.py"
    if not bootstrap.exists():
        return

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(repo_root)

    creation_flags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP - the subprocess does not
        # hold the Cowork console; the hook handler can exit immediately.
        creation_flags = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(
            [sys.executable, str(bootstrap), "sync"],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
    except (OSError, FileNotFoundError):
        # Background sync is best-effort; failure is non-fatal.
        return


HANDLERS = {
    "SessionStart": handle_session_start,
    "UserPromptSubmit": handle_user_prompt_submit,
    "PreToolUse": handle_pre_tool_use,
    "PermissionRequest": handle_permission_request,
    "PostToolUse": handle_post_tool_use,
    "PostToolUseFailure": handle_post_tool_use_failure,
    "PreCompact": handle_pre_compact,
    "PostCompact": handle_post_compact,
    "SubagentStop": handle_subagent_stop,
    "Stop": handle_stop,
    "SessionEnd": handle_session_end,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    event_name = argv[0] if argv else ""
    event = read_hook_input()
    event_name = event_name or str(event.get("hook_event_name") or "")
    handler = HANDLERS.get(event_name)
    if handler is None:
        return 0
    return handler(event)


if __name__ == "__main__":
    sys.exit(main())
