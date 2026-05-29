# SPDX-License-Identifier: Apache-2.0
"""Payload-mirror parity guard.

The pipeline-init payload (`skills/pipeline-init/references/pipeline-payload/`)
ships a copy of the canonical scripts/ and pipelines/ tree into a user's
project. Every payload file MUST be byte-identical to its canonical twin —
otherwise a scaffolded project silently runs stale roles/scripts/pipelines.

This regression bit the v3.0.0 train: WS-1 edited `pipelines/*.yaml` and WS-2
edited `pipelines/roles/{critic,drift-detector,verifier}.md` +
`scripts/{auto_promote,check_execute_readiness,check_stage_done}.py` on the
canonical side only, and nothing caught the divergence because the existing
byte-identical tests covered just executor.md / judge.md / classify_action.py /
action-classification.yaml. This test covers the WHOLE payload tree so any
future canonical-only edit fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_ROOT = (
    REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload"
)

# Payload files that legitimately have no canonical twin (none today). Keep this
# list empty unless a payload-only file is deliberately introduced; an entry
# here is a documented exception, not a place to hide a missed mirror update.
_PAYLOAD_ONLY_ALLOWLIST: frozenset[str] = frozenset()


def _payload_files() -> list[Path]:
    return sorted(p for p in PAYLOAD_ROOT.rglob("*") if p.is_file())


def test_payload_root_exists_and_is_populated():
    assert PAYLOAD_ROOT.is_dir(), f"payload root missing: {PAYLOAD_ROOT}"
    assert _payload_files(), "payload tree is empty — mirror layout changed?"


@pytest.mark.parametrize(
    "payload_file",
    _payload_files(),
    ids=lambda p: str(p.relative_to(PAYLOAD_ROOT)),
)
def test_every_payload_file_matches_its_canonical_twin(payload_file: Path):
    rel = payload_file.relative_to(PAYLOAD_ROOT).as_posix()
    canonical = REPO_ROOT / rel
    if rel in _PAYLOAD_ONLY_ALLOWLIST:
        assert not canonical.exists(), (
            f"{rel} is allowlisted as payload-only but a canonical twin exists; "
            "remove it from the allowlist."
        )
        return
    assert canonical.is_file(), (
        f"payload file {rel} has no canonical twin at {canonical} — either it is "
        "a new payload-only file (add to the allowlist) or the canonical copy was "
        "deleted without removing the mirror."
    )
    assert canonical.read_bytes() == payload_file.read_bytes(), (
        f"payload mirror diverged from canonical: {rel}. Regenerate the payload "
        "copy from the canonical file (they must be byte-identical)."
    )
