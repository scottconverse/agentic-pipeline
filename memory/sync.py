# SPDX-License-Identifier: Apache-2.0
"""Flush Layer A (file-backed) records into Layer B (Mem0).

Picks records from `.agent-runs/<run-id>/memory/*.jsonl` that carry a
valid `metadata.type` (per PRD FR-7 taxonomy) and forwards them via
PolicyLayer.add. Records without a valid type are skipped silently
(they remain in Layer A unconditionally).

The flush is idempotent enough for periodic invocation: each record
in events.jsonl gets a deterministic hash that's stored in a local
.mem0/synced-hashes.txt sidecar; previously-synced records are
skipped on subsequent flushes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .policy import PolicyLayer


@dataclass(frozen=True)
class SyncResult:
    candidates: int
    sent: int
    skipped_no_type: int
    skipped_already_sent: int
    rejected: int
    outboxed: int


def _record_fingerprint(record: dict) -> str:
    """Deterministic SHA-256 of (event, run_id, timestamp, message) so
    re-runs don't re-send the same record. Metadata is excluded so
    minor metadata edits don't bust the dedup hash."""
    payload = "|".join(
        str(record.get(k, ""))
        for k in ("event", "run_id", "timestamp", "message")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_synced(synced_path: Path) -> set[str]:
    if not synced_path.exists():
        return set()
    return {line.strip() for line in synced_path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _append_synced(synced_path: Path, fingerprints: list[str]) -> None:
    synced_path.parent.mkdir(parents=True, exist_ok=True)
    with synced_path.open("a", encoding="utf-8") as handle:
        for fp in fingerprints:
            handle.write(fp + "\n")


def flush_layer_a_to_mem0(repo_root: Path, policy: PolicyLayer) -> SyncResult:
    """Walk `.agent-runs/<run-id>/memory/events.jsonl` and forward typed
    records to Mem0 via the PolicyLayer."""
    runs_root = repo_root / ".agent-runs"
    if not runs_root.exists():
        return SyncResult(0, 0, 0, 0, 0, 0)

    synced_path = repo_root / ".mem0" / "synced-hashes.txt"
    already_synced = _load_synced(synced_path)
    new_fingerprints: list[str] = []

    candidates = 0
    sent = 0
    skipped_no_type = 0
    skipped_already = 0
    rejected = 0
    outboxed = 0

    allowed_types = set(policy.config.writes.allowed_types)

    for run_dir in sorted(runs_root.iterdir()):
        events_path = run_dir / "memory" / "events.jsonl"
        if not events_path.exists():
            continue
        for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            candidates += 1
            metadata = record.get("metadata") or {}
            record_type = str(metadata.get("type", ""))
            if record_type not in allowed_types:
                skipped_no_type += 1
                continue
            fingerprint = _record_fingerprint(record)
            if fingerprint in already_synced:
                skipped_already += 1
                continue
            message = str(record.get("message", ""))
            if not message:
                skipped_no_type += 1
                continue
            result = policy.add(
                message,
                metadata={
                    "type": record_type,
                    "source_event": record.get("event"),
                    "source_run_id": record.get("run_id"),
                    "source_timestamp": record.get("timestamp"),
                    **{k: v for k, v in metadata.items() if k != "type"},
                },
                include_run=True,
            )
            status = result.get("status")
            if status == "added":
                sent += 1
                new_fingerprints.append(fingerprint)
            elif status == "outboxed":
                outboxed += 1
                new_fingerprints.append(fingerprint)
            elif status == "rejected":
                rejected += 1
            # disabled / lost: no-op, will retry next flush

    if new_fingerprints:
        _append_synced(synced_path, new_fingerprints)

    return SyncResult(
        candidates=candidates,
        sent=sent,
        skipped_no_type=skipped_no_type,
        skipped_already_sent=skipped_already,
        rejected=rejected,
        outboxed=outboxed,
    )
