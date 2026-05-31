# SPDX-License-Identifier: Apache-2.0
"""Coverage for scripts/preflight_infrastructure.py (audit TEST-002).

Phase-0 preflight gates Phase-1 work and money-spending stages; it shipped at
0% coverage. These pin each check's pass/fail contract, the report rendering,
and main()'s exit codes (every check passes -> 0, any fails -> 1, bad module
root -> 2) so the gate can't silently invert.
"""
from __future__ import annotations

import sys

import pytest

from scripts.preflight_infrastructure import (
    check_cross_platform_mismatch,
    check_diagnostic_instrumentation,
    check_scripts_referenced_exist,
    check_verify_release_local,
    check_workflow_run_health,
    check_workflow_yaml_parse,
    main,
    render_report,
)


def _wf(root, name, text):
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / name).write_text(text, encoding="utf-8")


# --- Check 1: workflow YAML parse -------------------------------------------


def test_yaml_parse_no_workflows_passes(tmp_path):
    assert check_workflow_yaml_parse(tmp_path).passed


def test_yaml_parse_valid_passes(tmp_path):
    _wf(tmp_path, "ci.yml", "name: ci\non: push\njobs: {}\n")
    assert check_workflow_yaml_parse(tmp_path).passed


def test_yaml_parse_malformed_fails(tmp_path):
    _wf(tmp_path, "bad.yml", "name: ci\n  : : : [unclosed\n")
    res = check_workflow_yaml_parse(tmp_path)
    assert not res.passed
    assert any("FAIL" in d for d in res.details)


# --- Check 2: workflow run health -------------------------------------------


def test_run_health_skips_without_repo():
    assert check_workflow_run_health(None).passed


# --- Check 3: referenced scripts exist --------------------------------------


def test_referenced_scripts_no_workflows_passes(tmp_path):
    assert check_scripts_referenced_exist(tmp_path).passed


def test_referenced_scripts_present_passes(tmp_path):
    _wf(tmp_path, "ci.yml", "run: python scripts/build.py\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build.py").write_text("print('hi')\n", encoding="utf-8")
    assert check_scripts_referenced_exist(tmp_path).passed


def test_referenced_scripts_missing_fails(tmp_path):
    _wf(tmp_path, "ci.yml", "run: python scripts/ghost.py\n")
    res = check_scripts_referenced_exist(tmp_path)
    assert not res.passed
    assert any("ghost.py" in d for d in res.details)


def test_referenced_scripts_empty_file_fails(tmp_path):
    _wf(tmp_path, "ci.yml", "run: bash scripts/empty.py\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "empty.py").write_text("", encoding="utf-8")
    res = check_scripts_referenced_exist(tmp_path)
    assert not res.passed
    assert any("0 bytes" in d for d in res.details)


# --- Check 4: verify-release.sh local ---------------------------------------


def test_verify_release_no_script_passes(tmp_path):
    assert check_verify_release_local(tmp_path, run_local=False).passed


def test_verify_release_skips_without_run_local(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify-release.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    res = check_verify_release_local(tmp_path, run_local=False)
    assert res.passed
    assert any("--run-local" in d for d in res.details)


# --- Check 5: cross-platform mismatch ---------------------------------------


def test_cross_platform_clean_passes(tmp_path):
    _wf(tmp_path, "ci.yml", "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n")
    assert check_cross_platform_mismatch(tmp_path).passed


def test_cross_platform_windows_docker_fails(tmp_path):
    _wf(
        tmp_path,
        "ci.yml",
        "jobs:\n  a:\n    runs-on: windows-latest\n    steps:\n      - run: docker compose up -d\n",
    )
    res = check_cross_platform_mismatch(tmp_path)
    assert not res.passed


def test_cross_platform_ubuntu_innosetup_fails(tmp_path):
    _wf(
        tmp_path,
        "ci.yml",
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: ISCC setup.iss\n",
    )
    assert not check_cross_platform_mismatch(tmp_path).passed


# --- Check 6: diagnostic instrumentation ------------------------------------


def test_diagnostic_no_script_passes(tmp_path):
    assert check_diagnostic_instrumentation(tmp_path).passed


def test_diagnostic_with_log_dump_passes(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify-release.sh").write_text(
        "docker compose logs --tail 100\n", encoding="utf-8"
    )
    assert check_diagnostic_instrumentation(tmp_path).passed


def test_diagnostic_without_log_dump_fails(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify-release.sh").write_text("echo done\n", encoding="utf-8")
    assert not check_diagnostic_instrumentation(tmp_path).passed


# --- render_report + main ----------------------------------------------------


def test_render_report_counts(tmp_path):
    from scripts.preflight_infrastructure import CheckResult

    text = render_report([CheckResult("a", True), CheckResult("b", False)], "mod")
    assert "Phase 0 Preflight Audit — mod" in text
    assert "1/2 checks passed" in text
    assert "## a — PASS" in text and "## b — FAIL" in text


def test_main_bad_module_root_returns_2(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["p", "--module-root", str(tmp_path / "nope")])
    assert main() == 2


def test_main_all_pass_returns_0_and_writes_report(tmp_path, monkeypatch):
    out = tmp_path / "phase0-report.md"
    # Empty module root: no workflows, no verify-release.sh -> all checks pass.
    monkeypatch.setattr(
        sys, "argv", ["p", "--module-root", str(tmp_path), "--report", str(out)]
    )
    assert main() == 0
    assert out.exists()
    assert "checks passed" in out.read_text(encoding="utf-8")


def test_main_returns_1_when_a_check_fails(tmp_path, monkeypatch):
    # A workflow referencing a missing script fails Check 3 -> exit 1.
    _wf(tmp_path, "ci.yml", "run: python scripts/ghost.py\n")
    monkeypatch.setattr(sys, "argv", ["p", "--module-root", str(tmp_path)])
    assert main() == 1
