# SPDX-License-Identifier: Apache-2.0
"""Input-integrity gate tests (audit QA-002 + QA-003/ENG-005).

QA-002: the manifest reader used a hand-rolled subset parser that silently
re-interpreted malformed YAML. It now uses PyYAML and fails closed on a parse
error — pinned here.

QA-003/ENG-005: find_repo_root trusted CLAUDE_PROJECT_DIR unconditionally, so a
stale/wrong value pointed every gate at the wrong tree. It now validates the
value is an existing directory and falls through otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_manifest_schema as cms  # noqa: E402
import policy_utils  # noqa: E402


# --- QA-002: manifest reader fails closed on malformed YAML ------------------


def test_manifest_reader_rejects_malformed_yaml(tmp_path):
    m = tmp_path / "manifest.yaml"
    # A broken flow sequence: the old hand-rolled parser coerced this into the
    # scalar string "[[[broken"; PyYAML rejects it.
    m.write_text("pipeline_run:\n  expected_outputs: [[[broken\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cms._read_manifest(m)
    assert exc.value.code == 1


def test_manifest_reader_parses_wellformed_yaml(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(
        'pipeline_run:\n'
        '  goal: "close the race"\n'
        '  allowed_paths:\n'
        '    - src/\n'
        '    - tests/\n'
        '  expected_outputs: []\n',
        encoding="utf-8",
    )
    fields = cms._read_manifest(m)
    assert fields["goal"] == "close the race"
    assert fields["allowed_paths"] == ["src/", "tests/"]
    assert fields["expected_outputs"] == []


def test_manifest_stdlib_fallback_matches_for_wellformed(tmp_path):
    # The stdlib fallback (no-PyYAML path) must agree with PyYAML on a
    # well-formed manifest's flat fields.
    text = (
        "pipeline_run:\n"
        "  goal: ok\n"
        "  allowed_paths:\n"
        "    - src/\n"
    )
    assert cms._read_manifest_stdlib(text)["allowed_paths"] == ["src/"]


# --- QA-003/ENG-005: CLAUDE_PROJECT_DIR validation ---------------------------


def test_find_repo_root_ignores_nonexistent_env(monkeypatch, tmp_path):
    bad = tmp_path / "does-not-exist"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(bad))
    root = policy_utils.find_repo_root(str(REPO_ROOT / "scripts" / "policy_utils.py"))
    assert root != bad.resolve(), "a non-existent CLAUDE_PROJECT_DIR must not be trusted"


def test_find_repo_root_honors_valid_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert policy_utils.find_repo_root("ignored.py") == tmp_path.resolve()
