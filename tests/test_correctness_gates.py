# SPDX-License-Identifier: Apache-2.0
"""Correctness-gate hardening tests (audit ENG-006 + QA-004)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_critic_evidence as cce  # noqa: E402
from memory.adapter import build_adapter  # noqa: E402


def _has_citation(text: str) -> bool:
    return any(p.search(text) for p in cce.CITATION_PATTERNS)


def test_prose_is_not_a_citation():
    # ENG-006: ordinary prose must NOT satisfy the evidence gate (it used to —
    # the file-path heuristic matched "e.g.", "v3.0.0", "U.S.").
    for prose in (
        "UX: no findings (e.g. no UI touched).",
        "No UI changes (i.e. backend only).",
        "v3.0.0 was fine",
        "See the U.S. spec",
        "nothing of note etc.",
    ):
        assert not _has_citation(prose), f"prose wrongly counts as a citation: {prose!r}"


def test_real_citations_still_count():
    for cite in (
        "see scripts/check_allowed_paths.py",
        "skills/run/references/run.md:7",
        "manifest.yaml line 42",
        "`grep -n foo bar`",
    ):
        assert _has_citation(cite), f"real citation not recognized: {cite!r}"


def test_build_adapter_rejects_plain_dict():
    # QA-004: a plain mapping must raise a typed error naming the contract,
    # not an opaque AttributeError on `.enabled`.
    with pytest.raises(TypeError):
        build_adapter({})
