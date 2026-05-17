#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate agent stop, defer, skip, and final-response decisions.

This gate is for the agent's immediate decision procedure. It does not trust a
claimed blocker unless the control state and attached evidence support it.

Ported from agent-pipeline-codex v0.9.0 (scripts/agent_decision_gate.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.policy_utils import find_repo_root
    from scripts.check_pipeline_control_loop import (
        INVALID_STOP_CONDITIONS,
        VALID_STOP_CONDITIONS,
    )
    from scripts.stop_validator import active_state_files, validate_all_active_stops
    from scripts.scope_lock_utils import (
        find_term_owner,
        list_value,
        load_scope_lock,
        normalize_text,
        parse_release_plan,
        scalar_value,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    try:
        from policy_utils import find_repo_root  # type: ignore
        from check_pipeline_control_loop import (
            INVALID_STOP_CONDITIONS,
            VALID_STOP_CONDITIONS,
        )  # type: ignore
        from stop_validator import active_state_files, validate_all_active_stops  # type: ignore
        from scope_lock_utils import (  # type: ignore
            find_term_owner,
            list_value,
            load_scope_lock,
            normalize_text,
            parse_release_plan,
            scalar_value,
        )
    except ModuleNotFoundError:  # pragma: no cover - copied project package import
        from scripts.policy.policy_utils import find_repo_root  # type: ignore
        from scripts.policy.check_pipeline_control_loop import (  # type: ignore
            INVALID_STOP_CONDITIONS,
            VALID_STOP_CONDITIONS,
        )
        from scripts.policy.stop_validator import (  # type: ignore
            active_state_files,
            validate_all_active_stops,
        )
        from scripts.policy.scope_lock_utils import (  # type: ignore
            find_term_owner,
            list_value,
            load_scope_lock,
            normalize_text,
            parse_release_plan,
            scalar_value,
        )


INVALID_DECISION_REASONS = INVALID_STOP_CONDITIONS | {
    "could_trigger_ci",
    "inferred_blocker",
    "unverified_blocker",
    "unverified_blocker_or_risk",
    "unverified_actions_budget_risk",
    "successful_ci",
    "remote_ci_green",
}

INTENTS = {
    "final_response",
    "defer",
    "stop",
    "skip_push",
    "skip_ci",
    "pause",
    "ask_user",
    "compact",
    "handoff",
    "start_rung_work",
}
LEDGER_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class DecisionResult:
    allowed: bool
    intent: str
    claimed_stop_condition: str
    reason: str
    required_next_action: str = ""
    continuing_to: str = ""
    state_path: str = ""


def _read_state(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        fields[key] = value.strip()
    return fields


def _active_state_paths(run_dir: Path) -> list[Path]:
    return active_state_files(run_dir)


def _state_for_run(run_dir: Path, run_id: str | None) -> Path | None:
    if run_id:
        path = run_dir / run_id / "active-control-state.md"
        return path if path.exists() else None

    active = _active_state_paths(run_dir)
    if active:
        return active[0]
    return None


def _evidence_files_exist(evidence_files: list[str]) -> tuple[bool, list[str]]:
    missing = [item for item in evidence_files if not Path(item).exists()]
    return not missing, missing


def _evaluate_start_rung_work(
    run_dir: Path,
    run_id: str | None,
    claimed_rung: str,
    prompt_text: str,
    scope_amendment: str,
) -> DecisionResult:
    if not run_id:
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            "`start_rung_work` requires --run so the scope-lock can be checked",
        )
    if not claimed_rung:
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            "`start_rung_work` requires --claimed-rung",
        )
    try:
        _, lock = load_scope_lock(run_dir, run_id)
    except FileNotFoundError as exc:
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            f"scope-lock.yaml missing at {exc.args[0]}",
        )

    current_rung = scalar_value(lock, "current_rung")
    title = scalar_value(lock, "rung_title")
    if claimed_rung != current_rung:
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            f"SCOPE_CONFLICT: claimed rung v{claimed_rung} does not match scope-lock v{current_rung} {title}. Replan or get explicit scope amendment before editing.",
        )

    if scope_amendment and "scott explicitly amends" in normalize_text(scope_amendment):
        return DecisionResult(
            True,
            "start_rung_work",
            "scope_conflict",
            "start allowed by explicit recorded Scott scope amendment",
        )

    if not prompt_text:
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            "`start_rung_work` requires --prompt-text or an explicit recorded Scott scope amendment",
        )

    canonical_source = scalar_value(lock, "canonical_source")
    plan_path = run_dir.parent / canonical_source
    plan = parse_release_plan(plan_path) if plan_path.exists() else {}
    normalized_prompt = normalize_text(prompt_text)
    expanded_prompt = normalize_text(prompt_text.replace("-", " ").replace("_", " "))
    for term in list_value(lock, "forbidden_feature_terms_without_replan"):
        normalized_term = normalize_text(term)
        expanded_term = normalize_text(term.replace("-", " ").replace("_", " "))
        if (
            normalized_term not in normalized_prompt
            and expanded_term not in normalized_prompt
            and expanded_term not in expanded_prompt
        ):
            continue
        owner = find_term_owner(plan, term)
        owner_text = (
            f"; {term} belongs to v{owner}" if owner and owner != current_rung else ""
        )
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            f"SCOPE_CONFLICT: release-plan.md says v{current_rung} is {title}{owner_text}. Replan or get explicit scope amendment before editing.",
        )

    canonical_terms = [
        title,
        scalar_value(lock, "proves"),
        *list_value(lock, "allowed_feature_terms"),
    ]
    if prompt_text and not any(
        normalize_text(term) in normalized_prompt for term in canonical_terms if term
    ):
        return DecisionResult(
            False,
            "start_rung_work",
            "scope_conflict",
            f"SCOPE_CONFLICT: prompt does not match canonical v{current_rung} scope `{title}`. Replan or record an explicit Scott scope amendment before editing.",
        )

    return DecisionResult(
        True,
        "start_rung_work",
        "scope_conflict",
        f"start allowed by scope-lock v{current_rung} {title}",
    )


def evaluate_agent_decision(
    run_dir: Path,
    intent: str,
    claimed_stop_condition: str,
    evidence: list[str] | None = None,
    evidence_files: list[str] | None = None,
    run_id: str | None = None,
    require_active_run: bool = True,
    claimed_rung: str = "",
    prompt_text: str = "",
    scope_amendment: str = "",
) -> DecisionResult:
    evidence_files = evidence_files or []

    if intent not in INTENTS:
        return DecisionResult(
            False, intent, claimed_stop_condition, f"invalid intent `{intent}`"
        )

    if intent == "start_rung_work":
        return _evaluate_start_rung_work(
            run_dir,
            run_id,
            claimed_rung=claimed_rung,
            prompt_text=prompt_text,
            scope_amendment=scope_amendment,
        )

    final_results = validate_all_active_stops(
        run_dir, require_active_run=require_active_run
    )
    blocked_final = [result for result in final_results if not result.allowed]
    if blocked_final:
        result = blocked_final[0]
        return DecisionResult(
            False,
            intent,
            claimed_stop_condition,
            result.reason,
            required_next_action=result.next_required_action,
            continuing_to=result.continuing_to,
            state_path=str(result.state_path or ""),
        )

    if claimed_stop_condition in INVALID_DECISION_REASONS:
        return DecisionResult(
            False,
            intent,
            claimed_stop_condition,
            f"`{claimed_stop_condition}` is not a valid stop condition",
        )

    if claimed_stop_condition not in VALID_STOP_CONDITIONS:
        return DecisionResult(
            False,
            intent,
            claimed_stop_condition,
            f"`claimed_stop_condition` must be one of: {', '.join(sorted(VALID_STOP_CONDITIONS))}",
        )

    state_path = _state_for_run(run_dir, run_id)
    state = _read_state(state_path) if state_path else {}
    recorded_stop = state.get("stop_condition", "")

    files_ok, missing = _evidence_files_exist(evidence_files)
    if not files_ok:
        return DecisionResult(
            False,
            intent,
            claimed_stop_condition,
            "evidence file missing: " + ", ".join(missing),
            state_path=str(state_path or ""),
        )

    if recorded_stop != claimed_stop_condition and not evidence_files:
        return DecisionResult(
            False,
            intent,
            claimed_stop_condition,
            "claimed blocker requires an evidence file and does not match active control state",
            state_path=str(state_path or ""),
        )

    if recorded_stop == claimed_stop_condition:
        reason = (
            f"decision allowed by recorded stop condition `{claimed_stop_condition}`"
        )
    else:
        reason = f"decision allowed by evidence for `{claimed_stop_condition}`"

    return DecisionResult(
        True,
        intent,
        claimed_stop_condition,
        reason,
        required_next_action=state.get("next_required_action", ""),
        continuing_to=state.get("continuing_to", ""),
        state_path=str(state_path or ""),
    )


def write_decision_ledger(
    run_dir: Path, result: DecisionResult, run_id: str | None = None
) -> Path:
    state_path = (
        Path(result.state_path)
        if result.state_path
        else _state_for_run(run_dir, run_id)
    )
    if run_id:
        ledger_path = run_dir / run_id / "decision-ledger.ndjson"
    elif state_path:
        ledger_path = state_path.parent / "decision-ledger.ndjson"
    else:
        ledger_path = run_dir / "decision-ledger.ndjson"

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(result) | {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return ledger_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", action="version", version="agent-pipeline-claude 2.0.0"
    )
    parser.add_argument(
        "--run-dir",
        default=str(find_repo_root(__file__) / ".agent-runs"),
        help="Directory containing run subdirectories. Defaults to .agent-runs in this repo.",
    )
    parser.add_argument("--run", help="Run id under .agent-runs/.")
    parser.add_argument("--intent", required=True, choices=sorted(INTENTS))
    parser.add_argument("--claimed-stop-condition", default="scope_conflict")
    parser.add_argument("--claimed-rung", default="")
    parser.add_argument("--prompt-text", default="")
    parser.add_argument("--scope-amendment", default="")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--evidence-file", action="append", default=[])
    parser.add_argument("--write-ledger", action="store_true")
    parser.add_argument("--allow-no-active-run", action="store_true")
    args = parser.parse_args()

    result = evaluate_agent_decision(
        Path(args.run_dir),
        intent=args.intent,
        claimed_stop_condition=args.claimed_stop_condition,
        evidence=args.evidence,
        evidence_files=args.evidence_file,
        run_id=args.run,
        require_active_run=not args.allow_no_active_run,
        claimed_rung=args.claimed_rung,
        prompt_text=args.prompt_text,
        scope_amendment=args.scope_amendment,
    )

    ledger_path = None
    if args.write_ledger:
        ledger_path = write_decision_ledger(Path(args.run_dir), result, args.run)

    status = "ALLOW" if result.allowed else "BLOCK"
    print(f"agent_decision_gate: {status}")
    print(f"  intent: {result.intent}")
    print(f"  claimed_stop_condition: {result.claimed_stop_condition}")
    print(f"  reason: {result.reason}")
    if result.continuing_to:
        print(f"  continuing_to: {result.continuing_to}")
    if result.required_next_action:
        print(f"  required_next_action: {result.required_next_action}")
    if result.state_path:
        print(f"  state_path: {result.state_path}")
    if ledger_path:
        print(f"  ledger: {ledger_path}")

    return 0 if result.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
