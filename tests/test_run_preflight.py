# SPDX-License-Identifier: Apache-2.0
"""Coverage for scripts/run_preflight.py (audit TEST-002).

run_preflight is the money-gate: it runs manifest schema / target / path
checks AFTER the human APPROVE and BEFORE the researcher spends a model call.
It shipped at 0% coverage. These pin the sequencer contract — no --run is a
no-op (exit 0), all checks passing exits 0 with the PASSED marker, and a hard
check failure short-circuits and exits 1 with the FAILED marker.
"""
from __future__ import annotations

import sys

import scripts.run_preflight as rp
from scripts.run_preflight import main as run_preflight_main


def test_no_run_argument_is_noop(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_preflight.py"])
    assert run_preflight_main() == 0
    assert "no-op outside a pipeline run" in capsys.readouterr().out


def test_all_checks_pass_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(rp, "_run", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(sys, "argv", ["run_preflight.py", "--run", "r1"])
    assert run_preflight_main() == 0
    out = capsys.readouterr().out
    assert "PREFLIGHT: ALL CHECKS PASSED" in out


def test_hard_failure_exits_1_and_short_circuits(monkeypatch, capsys):
    calls: list[str] = []

    def fake_run(check_name, script_args, run_args):
        calls.append(check_name)
        return (False, "manifest missing required field")

    monkeypatch.setattr(rp, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_preflight.py", "--run", "r1"])
    assert run_preflight_main() == 1
    out = capsys.readouterr().out
    assert "PREFLIGHT: 1 CHECK(S) FAILED" in out
    # The first hard failure short-circuits — later checks don't run.
    assert calls == ["check_manifest_schema"]


def test_failed_report_lists_the_failing_check(monkeypatch, capsys):
    monkeypatch.setattr(rp, "_run", lambda *a, **k: (False, "bad"))
    monkeypatch.setattr(sys, "argv", ["run_preflight.py", "--run", "r1"])
    run_preflight_main()
    out = capsys.readouterr().out
    assert "research stage will NOT run" in out
    assert "[FAIL] check_manifest_schema" in out
