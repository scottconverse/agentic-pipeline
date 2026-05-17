#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared stop-condition truth validator for Agent Pipeline control gates.

Ported from agent-pipeline-codex v0.9.0 (scripts/stop_validator.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.check_pipeline_control_loop import (
        parse_control_state,
        validate_control_state,
    )
    from scripts.policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    try:
        from check_pipeline_control_loop import (
            parse_control_state,
            validate_control_state,
        )
        from policy_utils import find_repo_root
    except ModuleNotFoundError:  # pragma: no cover - copied project package import
        from scripts.policy.check_pipeline_control_loop import (
            parse_control_state,
            validate_control_state,
        )
        from scripts.policy.policy_utils import find_repo_root


VALID_HUMAN_GATE_STAGES = {"manifest", "plan", "manager"}
REPLAN_SIGNALS = ("replan", "manifest", "scope", "amend")


@dataclass(frozen=True)
class StopValidation:
    allowed: bool
    reason: str
    state_path: Path | None = None
    next_required_action: str = ""
    continuing_to: str = ""
    stop_condition: str = ""
    current_stage: str = ""


@dataclass(frozen=True)
class RunLogEvent:
    stage: str
    status: str
    note: str


def repo_root() -> Path:
    return find_repo_root(__file__)


def run_dir_from_state_path(state_path: Path) -> Path:
    return state_path.parent


def pipeline_path_for_run(run_dir: Path, fields: dict[str, str]) -> Path:
    manifest_type = _manifest_type(run_dir)
    return run_dir.parents[1] / ".pipelines" / f"{manifest_type or 'feature'}.yaml"


def discover_state_files(run_dir: Path) -> list[Path]:
    if not run_dir.exists():
        return []
    return sorted(run_dir.glob("*/active-control-state.md"))


def active_state_files(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in discover_state_files(run_dir):
        fields = parse_control_state(path.read_text(encoding="utf-8-sig"))
        if fields.get("active_run", "").lower() == "true":
            paths.append(path)
    return paths


def validate_state_file(state_path: Path) -> StopValidation:
    fields = parse_control_state(state_path.read_text(encoding="utf-8-sig"))
    violations = validate_control_state(fields)
    if violations:
        return StopValidation(
            allowed=False,
            reason="control-state validation failed: " + "; ".join(violations),
            state_path=state_path,
        )

    if fields.get("active_run", "").lower() != "true":
        return StopValidation(
            allowed=True, reason="inactive run", state_path=state_path
        )

    stop_condition = fields.get("stop_condition", "")
    current_stage = fields.get("current_stage", "")
    next_required_action = fields.get("next_required_action", "")
    continuing_to = fields.get("continuing_to", "")

    if fields.get("final_response_allowed", "").lower() == "false":
        return StopValidation(
            allowed=False,
            reason=f"active run must continue; stop_condition={stop_condition}",
            state_path=state_path,
            next_required_action=next_required_action,
            continuing_to=continuing_to,
            stop_condition=stop_condition,
            current_stage=current_stage,
        )

    run_dir = run_dir_from_state_path(state_path)
    stop_violation = _validate_specific_stop(run_dir, fields)
    if stop_violation:
        return StopValidation(
            allowed=False,
            reason=stop_violation,
            state_path=state_path,
            next_required_action=next_required_action,
            continuing_to=continuing_to,
            stop_condition=stop_condition,
            current_stage=current_stage,
        )

    return StopValidation(
        allowed=True,
        reason=f"valid stop condition recorded: {stop_condition}",
        state_path=state_path,
        next_required_action=next_required_action,
        continuing_to=continuing_to,
        stop_condition=stop_condition,
        current_stage=current_stage,
    )


def validate_all_active_stops(
    run_dir: Path, require_active_run: bool = False
) -> list[StopValidation]:
    states = discover_state_files(run_dir)
    if not states:
        if require_active_run:
            return [
                StopValidation(
                    allowed=False,
                    reason=f"no active-control-state.md files found under {run_dir}",
                )
            ]
        return [StopValidation(allowed=True, reason="no pipeline control state found")]

    results = [validate_state_file(path) for path in states]
    active = [
        result
        for result in results
        if result.state_path
        and parse_control_state(result.state_path.read_text(encoding="utf-8-sig"))
        .get("active_run", "")
        .lower()
        == "true"
    ]
    if require_active_run and not active:
        return [
            StopValidation(
                allowed=False,
                reason=f"no active run found in {run_dir}",
            )
        ]
    return results


def _validate_specific_stop(run_dir: Path, fields: dict[str, str]) -> str:
    stop_condition = fields.get("stop_condition", "")
    current_stage = fields.get("current_stage", "")
    next_required_action = fields.get("next_required_action", "")
    continuing_to = fields.get("continuing_to", "")

    if stop_condition == "human_approval_gate":
        return _validate_human_gate(run_dir, current_stage)
    if stop_condition == "scope_conflict":
        return _validate_scope_conflict(
            run_dir, current_stage, next_required_action, continuing_to
        )
    if stop_condition == "failed_gate_needs_user_direction":
        return _validate_failed_gate(run_dir, current_stage)
    if stop_condition == "credential_or_secret_required":
        return _require_text_signal(
            "credential_or_secret_required",
            (next_required_action, continuing_to),
            ("credential", "secret", "token", "key"),
        )
    if stop_condition == "destructive_action":
        return _require_text_signal(
            "destructive_action",
            (next_required_action, continuing_to),
            ("delete", "destructive", "force", "reset", "drop", "destroy"),
        )
    if stop_condition == "external_system_unavailable_after_retry":
        return _require_text_signal(
            "external_system_unavailable_after_retry",
            (next_required_action, continuing_to),
            ("unavailable", "after retry", "retry", "external", "service"),
        )
    if stop_condition == "user_explicitly_paused_or_stopped":
        return _require_text_signal(
            "user_explicitly_paused_or_stopped",
            (next_required_action, continuing_to),
            ("user", "explicit", "pause", "paused", "stop", "stopped"),
        )
    return ""


def _validate_human_gate(run_dir: Path, current_stage: str) -> str:
    if current_stage not in VALID_HUMAN_GATE_STAGES:
        return (
            f"human_approval_gate is only valid at {sorted(VALID_HUMAN_GATE_STAGES)}; "
            f"current_stage={current_stage!r}"
        )

    resume = _resume_stage(run_dir)
    if resume and resume != current_stage:
        return (
            f"human_approval_gate is stale: run.log resumes at {resume!r}, "
            f"but control state says {current_stage!r}"
        )
    return ""


def _validate_scope_conflict(
    run_dir: Path,
    current_stage: str,
    next_required_action: str,
    continuing_to: str,
) -> str:
    if _scope_lock_receipt_passes(run_dir):
        action_text = " ".join(
            [current_stage, next_required_action, continuing_to]
        ).lower()
        if "scope-authority" in action_text and "repair" in action_text:
            return (
                "stale scope_conflict stop condition: scope-lock receipt exists, "
                "so scope-authority repair can no longer authorize a final response"
            )
        if not any(signal in action_text for signal in REPLAN_SIGNALS):
            return (
                "scope_conflict stop condition lacks a current replan/scope action, "
                "and scope-lock receipt already passes"
            )
    return ""


def _validate_failed_gate(run_dir: Path, current_stage: str) -> str:
    events = _read_run_log(run_dir)
    if events and not any(
        event.stage == current_stage and event.status in {"FAILED", "BLOCKED"}
        for event in events
    ):
        return (
            "failed_gate_needs_user_direction is unproven: run.log has no FAILED/BLOCKED "
            f"event for current_stage={current_stage!r}"
        )
    return ""


def _require_text_signal(
    stop_condition: str, values: tuple[str, ...], needles: tuple[str, ...]
) -> str:
    haystack = " ".join(values).lower()
    if any(needle in haystack for needle in needles):
        return ""
    return f"{stop_condition} is unproven: next_required_action/continuing_to lack a matching signal"


def _scope_lock_receipt_passes(run_dir: Path) -> bool:
    receipt = run_dir / "scope-lock-receipt.txt"
    if not receipt.exists():
        return False
    return "scope_lock: PASS" in receipt.read_text(encoding="utf-8-sig")


def _manifest_type(run_dir: Path) -> str:
    manifest = run_dir / "manifest.yaml"
    if not manifest.exists():
        return ""
    for raw in manifest.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw.strip()
        if stripped.startswith("type:"):
            return stripped.partition(":")[2].strip().strip("\"'")
    return ""


def _pipeline_stages(run_dir: Path) -> list[str]:
    pipeline = pipeline_path_for_run(run_dir, {})
    if not pipeline.exists():
        return []
    stages: list[str] = []
    in_stages = False
    for raw in pipeline.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw.strip()
        if stripped == "stages:":
            in_stages = True
            continue
        if not in_stages:
            continue
        if stripped.startswith("- name:"):
            stages.append(stripped.partition(":")[2].strip().strip("\"'"))
    return stages


def _read_run_log(run_dir: Path) -> list[RunLogEvent]:
    log = run_dir / "run.log"
    if not log.exists():
        return []
    events: list[RunLogEvent] = []
    for raw in log.read_text(encoding="utf-8-sig").splitlines():
        parts = [part.strip() for part in raw.split("|", 3)]
        if len(parts) != 4:
            continue
        _, stage, status, note = parts
        events.append(RunLogEvent(stage=stage, status=status, note=note))
    return events


def _resume_stage(run_dir: Path) -> str:
    stages = _pipeline_stages(run_dir)
    if not stages:
        return ""
    completed = {
        event.stage for event in _read_run_log(run_dir) if event.status == "COMPLETE"
    }
    for stage in stages:
        if stage not in completed:
            return stage
    return ""
