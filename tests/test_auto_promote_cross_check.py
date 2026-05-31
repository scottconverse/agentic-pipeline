# SPDX-License-Identifier: Apache-2.0
"""Auto-promote body cross-checks + high-risk backstop (audit ENG-007).

The pre-fix gate trusted agent-authored count lines and only checked their
internal arithmetic — so a report could declare `0 blocker, 0 critical` over a
body that lists blockers and still auto-promote past the human manager gate.
These tests pin the fix: a clean count line that the report *body* contradicts
fails the condition closed, and high-risk manifests never auto-promote.
"""
from __future__ import annotations

from scripts.auto_promote import (
    _check_critic,
    _check_risk_gate,
    _check_verifier,
)


# --- critic body cross-check -------------------------------------------------

_CLEAN_CRITIC = """\
**Findings: 0 total, 0 blocker, 0 critical, 0 major, 0 minor**

## Blocker findings
None.

## Critical findings
None.

## Recommended manager verdict
PROMOTE — no blocking findings.
"""


def test_critic_clean_passes(tmp_path):
    (tmp_path / "critic-report.md").write_text(_CLEAN_CRITIC, encoding="utf-8")
    res = _check_critic(tmp_path)
    assert res.passed, res.evidence


def test_critic_block_verdict_over_clean_count_fails(tmp_path):
    report = (
        "**Findings: 0 total, 0 blocker, 0 critical, 0 major, 0 minor**\n\n"
        "## Recommended manager verdict\nBLOCK — work cannot ship.\n"
    )
    (tmp_path / "critic-report.md").write_text(report, encoding="utf-8")
    res = _check_critic(tmp_path)
    assert not res.passed
    assert "BLOCK" in res.evidence and "ENG-007" in res.evidence


def test_critic_finding_ids_over_clean_count_fails(tmp_path):
    report = (
        "**Findings: 0 total, 0 blocker, 0 critical, 0 major, 0 minor**\n\n"
        "## Blocker findings\n\n"
        "### C-1 — Secrets written to logs\nEvidence: foo.py:10\n\n"
        "## Recommended manager verdict\nPROMOTE\n"
    )
    (tmp_path / "critic-report.md").write_text(report, encoding="utf-8")
    res = _check_critic(tmp_path)
    assert not res.passed
    assert "C-1" in res.evidence


# --- verifier body cross-check ----------------------------------------------


def test_verifier_clean_passes(tmp_path):
    report = (
        "**Criteria: 2 total, 2 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**\n\n"
        "## Criteria\n- **MET**: one\n- **MET**: two\n"
    )
    (tmp_path / "verifier-report.md").write_text(report, encoding="utf-8")
    res = _check_verifier(tmp_path)
    assert res.passed, res.evidence


def test_verifier_not_met_in_body_over_clean_count_fails(tmp_path):
    # Count line declares 0 NOT MET (arithmetic-consistent), but the body
    # carries a literal `- **NOT MET**:` criterion.
    report = (
        "**Criteria: 2 total, 2 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**\n\n"
        "## Criteria\n- **MET**: one\n- **NOT MET**: two never landed\n"
    )
    (tmp_path / "verifier-report.md").write_text(report, encoding="utf-8")
    res = _check_verifier(tmp_path)
    assert not res.passed
    assert "NOT MET" in res.evidence and "ENG-007" in res.evidence


# --- high-risk backstop ------------------------------------------------------


def test_high_risk_manifest_blocks_auto_promote(tmp_path):
    (tmp_path / "manifest.yaml").write_text(
        "pipeline_run:\n  risk: high\n", encoding="utf-8"
    )
    res = _check_risk_gate(tmp_path)
    assert not res.passed
    assert "high" in res.evidence.lower()


def test_low_risk_manifest_is_eligible(tmp_path):
    (tmp_path / "manifest.yaml").write_text(
        "pipeline_run:\n  risk: low\n", encoding="utf-8"
    )
    assert _check_risk_gate(tmp_path).passed


def test_missing_manifest_is_eligible(tmp_path):
    # No manifest -> risk gate not applicable (low/medium-style runs).
    assert _check_risk_gate(tmp_path).passed
