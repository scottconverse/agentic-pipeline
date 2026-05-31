# SPDX-License-Identifier: Apache-2.0
"""Redaction performance regression (audit QA-R-001).

The v3.0.1 URL-credential pattern backtracked quadratically; a ~200KB benign
input hung scrub() >15s on the memory-write path. These pin that scrub() stays
fast on large/adversarial inputs while still blocking a real credential URL.
"""
from __future__ import annotations

import time

from memory.redaction import scrub


def _elapsed(text: str) -> float:
    start = time.perf_counter()
    scrub(text)
    return time.perf_counter() - start


def test_scrub_large_benign_input_is_fast():
    # The QA-R-001 repro: 200KB of a single char must not hang.
    assert _elapsed("a" * 200_000) < 2.0
    # scheme-prefixed, no '@' — worst case for the URL-credential pattern.
    assert _elapsed("http://" + "a" * 100_000) < 2.0
    assert _elapsed("Bearer " + "a" * 100_000) < 2.0


def test_scrub_still_blocks_credential_url():
    assert scrub("postgres://dbuser:s3cr3tp4ss@db.internal:5432/app").allowed is False
