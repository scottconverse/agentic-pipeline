# SPDX-License-Identifier: Apache-2.0
"""agent_decision_gate exit-code coverage (audit TEST-001 residual).

The prior re-audit noted the verdict->exit-code mapping in main() was
import-smoke only — the gated-stop architecture rests on it. These pin that
main() returns 0 on ALLOW and 1 on BLOCK, and that the real evaluator returns a
blocking result for an unrecognized intent.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import agent_decision_gate as adg  # noqa: E402


def _stub(allowed: bool):
    return types.SimpleNamespace(
        allowed=allowed,
        intent="stop",
        claimed_stop_condition="scope_conflict",
        reason="stub",
        continuing_to="",
        required_next_action="",
        state_path=None,
    )


def test_main_returns_1_on_block(monkeypatch):
    monkeypatch.setattr(adg, "evaluate_agent_decision", lambda *a, **k: _stub(False))
    monkeypatch.setattr(sys, "argv", ["agent_decision_gate", "--intent", "stop"])
    assert adg.main() == 1


def test_main_returns_0_on_allow(monkeypatch):
    monkeypatch.setattr(adg, "evaluate_agent_decision", lambda *a, **k: _stub(True))
    monkeypatch.setattr(sys, "argv", ["agent_decision_gate", "--intent", "stop"])
    assert adg.main() == 0


def test_evaluate_blocks_unrecognized_intent(tmp_path):
    res = adg.evaluate_agent_decision(
        tmp_path / ".agent-runs",
        intent="not_a_real_intent",
        claimed_stop_condition="scope_conflict",
        evidence=[],
        evidence_files=[],
        run_id="r",
        require_active_run=False,
        claimed_rung="",
        prompt_text="",
        scope_amendment="",
    )
    assert res.allowed is False
