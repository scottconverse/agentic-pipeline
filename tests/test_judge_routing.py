# SPDX-License-Identifier: Apache-2.0
"""v0.4 judge-routing tests.

Pins the four behaviors the judge-reincorporation manifest requires of the
deterministic PreToolUse hook during a judged run (one where
`.pipelines/action-classification.yaml` exists, surfaced as
`ActiveRun.judge_active`):

  * deny-redirect — an external-facing or release-class action is upgraded
    from warn to a JUDGE_REVIEW_REQUIRED deny, making the executor's
    stop-and-propose protocol non-bypassable.
  * one-shot consume — a `judge-approved-next.txt` sidecar that matches the
    EXACT command exempts that single call and is then deleted, so a second
    identical command is re-denied.
  * floor precedence — the absolute destructive/secret deny floor is
    evaluated first and the approval sidecar can NEVER reopen it.
  * judged-run gate — when the run is not judged (no classification file,
    drafting, or no active run) external actions stay at warn, unchanged.

Plus the hook_runner integration: a judge-routing deny is tagged distinctly
(`rule: judge_routing`) so it is observable separately from a generic
content-risk deny.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hooks import hook_runner
from hooks.hook_utils import (
    ActiveRun,
    _JUDGE_APPROVED_SIDECAR,
    classify_tool_risk,
    consume_judge_approval_on_bash_success,
    permission_decision,
)


def _make_run(
    run_dir: Path,
    *,
    judge_active: bool = True,
    drafting: bool = False,
    run_id: str | None = None,
) -> ActiveRun:
    """Build a synthetic ActiveRun anchored at ``run_dir``."""
    rd = run_dir.resolve()
    rd.mkdir(parents=True, exist_ok=True)
    state = rd / "active-control-state.md"
    state.write_text(
        "active_run: " + ("drafting" if drafting else "true") + "\n",
        encoding="utf-8",
    )
    return ActiveRun(
        run_id=run_id or rd.name,
        run_dir=rd,
        state_path=state,
        fields={"active_run": "drafting" if drafting else "true"},
        directive_bound=False,
        judge_active=judge_active,
        is_drafting=drafting,
    )


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _write_approval(run: ActiveRun, command: str) -> None:
    (run.run_dir / _JUDGE_APPROVED_SIDECAR).write_text(command, encoding="utf-8")


def _approval_exists(run: ActiveRun) -> bool:
    return (run.run_dir / _JUDGE_APPROVED_SIDECAR).exists()


# ---------------------------------------------------------------------------
# 1. deny-redirect
# ---------------------------------------------------------------------------


EXTERNAL_COMMANDS = [
    "git push origin feature/x",
    "gh pr create --fill",
    "gh pr merge 42",
    "gh release create v1.0.0",
    "curl -X POST https://api.example.com/deploy",
]


@pytest.mark.parametrize("command", EXTERNAL_COMMANDS)
def test_judged_run_denies_external_action_with_redirect(
    tmp_path: Path, command: str
) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-1")
    severity, reasons = classify_tool_risk(_bash(command), [run])
    assert severity == "deny"
    joined = " ".join(reasons)
    assert "JUDGE_REVIEW_REQUIRED" in joined
    assert "pending-action.yaml" in joined


def test_redirect_reason_names_the_stop_and_propose_protocol(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-1b")
    _, reasons = classify_tool_risk(_bash("git push origin main"), [run])
    joined = " ".join(reasons)
    # The deny must point the executor at the protocol, not just refuse.
    assert "stop-and-propose" in joined
    assert "judge-approved-next.txt" in joined


# ---------------------------------------------------------------------------
# 2. one-shot consume
# ---------------------------------------------------------------------------


def test_matching_sidecar_exempts_the_command(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-2")
    command = "git push origin feature/judge"
    _write_approval(run, command)
    severity, reasons = classify_tool_risk(_bash(command), [run])
    # Exempted: falls back to the ordinary external warn, not a judge deny.
    assert severity == "warn"
    assert "JUDGE_REVIEW_REQUIRED" not in " ".join(reasons)


def test_classify_is_read_only_and_does_not_consume(tmp_path: Path) -> None:
    # classify_tool_risk runs for BOTH the PreToolUse and PermissionRequest
    # events, so it must never mutate the sidecar — repeated classification
    # of an approved command stays exempt and leaves the sidecar in place.
    run = _make_run(tmp_path / ".agent-runs" / "judged-2b")
    command = "git push origin feature/judge"
    _write_approval(run, command)

    first_severity, _ = classify_tool_risk(_bash(command), [run])
    second_severity, _ = classify_tool_risk(_bash(command), [run])
    assert first_severity == "warn"
    assert second_severity == "warn"
    assert _approval_exists(run)


def test_consume_on_bash_success_makes_the_exemption_one_shot(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-2b2")
    command = "git push origin feature/judge"
    _write_approval(run, command)

    # PreToolUse exempts (warn) but does not consume.
    assert classify_tool_risk(_bash(command), [run])[0] == "warn"
    assert _approval_exists(run)

    # PostToolUse success consumes the sidecar exactly once.
    consumed = consume_judge_approval_on_bash_success(_bash(command), [run])
    assert consumed == command
    assert not _approval_exists(run)

    # A second attempt at the same command has no sidecar -> re-denied.
    severity, reasons = classify_tool_risk(_bash(command), [run])
    assert severity == "deny"
    assert "JUDGE_REVIEW_REQUIRED" in " ".join(reasons)


def test_consume_requires_an_exact_match(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-2b3")
    _write_approval(run, "git push origin main")
    consumed = consume_judge_approval_on_bash_success(
        _bash("git push origin other"), [run]
    )
    assert consumed is None
    assert _approval_exists(run)


def test_consume_skips_drafting_and_unjudged_runs(tmp_path: Path) -> None:
    command = "git push origin feature/judge"
    drafting = _make_run(
        tmp_path / ".agent-runs" / "draft", judge_active=True, drafting=True
    )
    unjudged = _make_run(tmp_path / ".agent-runs" / "unjudged-2", judge_active=False)
    _write_approval(drafting, command)
    _write_approval(unjudged, command)
    assert consume_judge_approval_on_bash_success(_bash(command), [drafting]) is None
    assert consume_judge_approval_on_bash_success(_bash(command), [unjudged]) is None
    assert _approval_exists(drafting)
    assert _approval_exists(unjudged)


def test_sidecar_must_match_exactly(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-2c")
    _write_approval(run, "git push origin main")
    # A different command is NOT exempted by a non-matching sidecar.
    severity, reasons = classify_tool_risk(_bash("git push origin other"), [run])
    assert severity == "deny"
    assert "JUDGE_REVIEW_REQUIRED" in " ".join(reasons)
    # And the non-matching approval is left intact.
    assert _approval_exists(run)


def test_sidecar_whitespace_is_stripped_before_match(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-2d")
    command = "git push origin feature/judge"
    _write_approval(run, "  " + command + "\n")
    # Read-side match honors a whitespace-padded sidecar.
    assert classify_tool_risk(_bash(command), [run])[0] == "warn"
    # Consume-side match honors it too, and clears it.
    assert consume_judge_approval_on_bash_success(_bash(command), [run]) == command
    assert not _approval_exists(run)


# ---------------------------------------------------------------------------
# 3. floor precedence — the destructive/secret deny floor precedes the judge
# ---------------------------------------------------------------------------


def test_destructive_floor_precedes_judge(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-3")
    # Force-push matches BOTH the destructive floor and the external pattern.
    severity, reasons = classify_tool_risk(
        _bash("git push --force origin main"), [run]
    )
    assert severity == "deny"
    joined = " ".join(reasons)
    assert "destructive" in joined
    # The floor wins: this is NOT a judge-routing redirect.
    assert "JUDGE_REVIEW_REQUIRED" not in joined


def test_approval_sidecar_never_reopens_the_destructive_floor(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-3b")
    command = "git push --force origin main"
    _write_approval(run, command)
    severity, reasons = classify_tool_risk(_bash(command), [run])
    # Even with an exact-match approval, a floored command stays denied: the
    # judge branch only runs when severity is not already deny, so the
    # destructive floor wins and the approval is never even read for it.
    assert severity == "deny"
    assert "destructive" in " ".join(reasons)
    assert _approval_exists(run)


def test_secret_floor_precedes_judge(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-3c")
    severity, reasons = classify_tool_risk(_bash("cat .env"), [run])
    assert severity == "deny"
    joined = " ".join(reasons)
    assert "credential or secret" in joined
    assert "JUDGE_REVIEW_REQUIRED" not in joined


# ---------------------------------------------------------------------------
# 4. judged-run gate — routing only fires for a live, non-drafting judged run
# ---------------------------------------------------------------------------


def test_external_action_only_warns_when_judge_inactive(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "unjudged", judge_active=False)
    severity, reasons = classify_tool_risk(_bash("git push origin main"), [run])
    assert severity == "warn"
    assert "JUDGE_REVIEW_REQUIRED" not in " ".join(reasons)


def test_external_action_only_warns_while_drafting(tmp_path: Path) -> None:
    run = _make_run(
        tmp_path / ".agent-runs" / "drafting", judge_active=True, drafting=True
    )
    severity, reasons = classify_tool_risk(_bash("git push origin main"), [run])
    assert severity == "warn"
    assert "JUDGE_REVIEW_REQUIRED" not in " ".join(reasons)


def test_external_action_only_warns_with_no_active_run(tmp_path: Path) -> None:
    severity, reasons = classify_tool_risk(_bash("git push origin main"), [])
    assert severity == "warn"
    assert "JUDGE_REVIEW_REQUIRED" not in " ".join(reasons)


def test_read_only_action_unaffected_by_judge_routing(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-4")
    severity, reasons = classify_tool_risk(_bash("git status"), [run])
    assert severity == "allow"
    assert "JUDGE_REVIEW_REQUIRED" not in " ".join(reasons)


# ---------------------------------------------------------------------------
# PermissionRequest path — classify_tool_risk's second caller must be read-only
# ---------------------------------------------------------------------------


def test_permission_request_path_does_not_consume_the_sidecar(tmp_path: Path) -> None:
    # permission_decision (the PermissionRequest hook) also calls
    # classify_tool_risk. An approved external command must NOT be denied
    # there, and the one-shot sidecar must survive for the PostToolUse
    # consume — otherwise whichever event fires first would burn it.
    run = _make_run(tmp_path / ".agent-runs" / "judged-perm")
    command = "git push origin feature/judge"
    _write_approval(run, command)
    decision = permission_decision(_bash(command), [run])
    assert decision is None
    assert _approval_exists(run)


def test_permission_request_denies_unapproved_external(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "judged-perm2")
    decision = permission_decision(_bash("git push origin main"), [run])
    assert decision is not None
    message = decision["hookSpecificOutput"]["decision"]["message"]
    assert "JUDGE_REVIEW_REQUIRED" in message


# ---------------------------------------------------------------------------
# hook_runner integration — judge-routing denies are tagged distinctly
# ---------------------------------------------------------------------------


def _setup_active_judged_run(root: Path) -> Path:
    run = root / ".agent-runs" / "judged-int"
    run.mkdir(parents=True)
    (run / "active-control-state.md").write_text(
        "\n".join(
            [
                "active_run: true",
                "current_stage: execute",
                "final_response_allowed: false",
            ]
        ),
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text(
        "allowed_paths:\n  - src\nforbidden_paths: []\n", encoding="utf-8"
    )
    pipelines = root / ".pipelines"
    pipelines.mkdir()
    (pipelines / "action-classification.yaml").write_text(
        "classification: {}\n", encoding="utf-8"
    )
    return run


def test_handle_pre_tool_use_denies_and_tags_judge_routing(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_judged_run(tmp_path)

    event = {"cwd": str(tmp_path), "tool_input": {"command": "git push origin main"}}
    assert hook_runner.handle_pre_tool_use(event) == 0

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "JUDGE_REVIEW_REQUIRED" in (
        payload["hookSpecificOutput"]["permissionDecisionReason"]
    )

    events = (run / "hook-events.jsonl").read_text(encoding="utf-8")
    assert "judge_routing deny" in events


def test_handle_pre_tool_use_tags_generic_risk_deny_separately(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = _setup_active_judged_run(tmp_path)

    # A destructive floor deny must NOT be tagged as judge_routing.
    event = {
        "cwd": str(tmp_path),
        "tool_input": {"command": "git reset --hard HEAD"},
    }
    assert hook_runner.handle_pre_tool_use(event) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    events = (run / "hook-events.jsonl").read_text(encoding="utf-8")
    assert "risk_classifier deny" in events
    assert "judge_routing deny" not in events
