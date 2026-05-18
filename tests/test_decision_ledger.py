# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_decision_ledger.py).

from scripts.check_decision_ledger import validate_ledger
from scripts.agent_decision_gate import DecisionResult, write_decision_ledger


def test_decision_ledger_accepts_schema_v1_rows(tmp_path) -> None:
    ledger = tmp_path / "decision-ledger.ndjson"
    ledger.write_text(
        '{"allowed": true, "claimed_stop_condition": "user_explicitly_says_pause_or_stop", '
        '"intent": "pause", "reason": "user asked", "schema_version": "1", '
        '"timestamp": "2026-05-13T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    assert validate_ledger(ledger) == []


def test_decision_ledger_rejects_invalid_rows(tmp_path) -> None:
    ledger = tmp_path / "decision-ledger.ndjson"
    ledger.write_text('{"allowed": "yes"}\n', encoding="utf-8")

    violations = validate_ledger(ledger)

    assert any("`allowed` must be bool" in item for item in violations)
    assert any("`intent` must be str" in item for item in violations)


def test_agent_decision_gate_written_ledger_validates(tmp_path) -> None:
    run_base = tmp_path / ".agent-runs"
    result = DecisionResult(
        allowed=True,
        intent="pause",
        claimed_stop_condition="user_explicitly_paused_or_stopped",
        reason="decision allowed by recorded stop condition",
    )

    ledger = write_decision_ledger(run_base, result, run_id="sample-run")

    assert validate_ledger(ledger) == []
