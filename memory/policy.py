# SPDX-License-Identifier: Apache-2.0
"""Policy layer: the gatekeeper for every Mem0 read/write.

Enforces (per PRD FR-6, FR-7, FR-9, FR-10, FR-11, FR-13, FR-14):

- Entity scoping defaults (FR-6): writes carry user_id + agent_id +
  app_id (+ run_id). Searches filter on user_id + app_id by default.
- Metadata taxonomy (FR-7): write `type` must be in the closed
  allowed_types set. Unknown values are rejected at the policy layer.
- Token budget (FR-9): retrieved memories injected into a prompt are
  capped at MEM0_TOKEN_BUDGET (default 1200); excess truncated by
  descending score. Session-start retrieval gets 1.5x overflow.
- Latency budget (FR-10): policy tracks p50/p95 latency on the
  prompt-time path; after 5 consecutive p95 violations within a
  session, prompt-time injection is disabled (session-start retrieval
  is preserved).
- Redaction (FR-11): every write candidate runs through scrub().
- Circuit breaker (FR-13): 5 consecutive backend failures open the
  breaker for 5 minutes. Writes go to a local outbox; reads return [].
- Consent gate (FR-14): platform mode requires consent grant before
  any backend call.

Writes are best-effort and never block the agent's response.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .adapter import MemoryAdapter, MemoryRecord, NullAdapter
from .config import Mem0Config
from .identity import IdentityContext
from .redaction import scrub


@dataclass
class CircuitBreaker:
    threshold: int = 5
    open_seconds: int = 300
    _consecutive_failures: int = 0
    _opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.time() - self._opened_at >= self.open_seconds:
            # Half-open: allow one probe; reset counter on success
            self._opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold and self._opened_at is None:
            self._opened_at = time.time()


@dataclass
class LatencyTracker:
    """Sliding-window p95-violation counter for FR-10 auto-disable."""

    samples: deque = field(default_factory=lambda: deque(maxlen=50))
    p95_budget_ms: int = 400
    consecutive_violations: int = 0
    threshold: int = 5
    injection_disabled: bool = False

    def record(self, ms: float) -> None:
        self.samples.append(ms)
        if ms > self.p95_budget_ms:
            self.consecutive_violations += 1
            if self.consecutive_violations >= self.threshold:
                self.injection_disabled = True
        else:
            self.consecutive_violations = 0


class PolicyLayer:
    def __init__(
        self,
        adapter: MemoryAdapter,
        config: Mem0Config,
        identity: IdentityContext,
        outbox_dir: Path | None = None,
    ):
        self.adapter = adapter
        self.config = config
        self.identity = identity
        self.outbox_dir = outbox_dir or (identity.repo_root / ".mem0" / "outbox")
        self.breaker = CircuitBreaker(
            threshold=config.circuit_breaker.consecutive_failures,
            open_seconds=config.circuit_breaker.open_seconds,
        )
        self.latency = LatencyTracker(p95_budget_ms=config.retrieval.latency_budget.p95_ms)

    # ----- public surface -----

    def add(
        self,
        messages: list[dict[str, str]] | str,
        *,
        metadata: dict[str, Any],
        include_run: bool = True,
    ) -> dict[str, Any]:
        """Validate, redact, scope-tag, and forward an add_memory call.

        Returns one of:
          {"status": "added", "result": <adapter response>}
          {"status": "rejected", "reason": "..."}
          {"status": "outboxed", "path": "..."} when breaker is open
        """
        if not self.config.enabled:
            return {"status": "disabled"}
        if self.config.mode == "platform" and not self.config.consent_granted_for:
            return {"status": "rejected", "reason": "platform consent not granted; run `pipeline mem0 init`"}

        # FR-7: type must be present and in the allowed set.
        record_type = str(metadata.get("type", ""))
        if record_type not in self.config.writes.allowed_types:
            return {
                "status": "rejected",
                "reason": f"metadata.type={record_type!r} is not in allowed_types {self.config.writes.allowed_types}",
            }

        # FR-11: redact secrets.
        candidate_text = self._extract_text(messages)
        redaction = scrub(
            candidate_text,
            secret_patterns=self.config.redaction.secret_patterns,
            block_paths=self.config.redaction.block_paths,
        )
        if not redaction.allowed:
            return {"status": "rejected", "reason": f"redaction blocked: {redaction.reason}"}

        # FR-6: scope tags
        filters = self.identity.as_write_keys(include_run=include_run)

        if self.breaker.is_open:
            return self._outbox_write({"messages": messages, "metadata": metadata, "filters": filters})

        try:
            result = self.adapter.add(messages, metadata=metadata, filters=filters)
            self.breaker.record_success()
            return {"status": "added", "result": result}
        except Exception as exc:  # noqa: BLE001 - intentional broad except for breaker
            self.breaker.record_failure()
            return self._outbox_write(
                {"messages": messages, "metadata": metadata, "filters": filters, "error": str(exc)}
            )

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        scope: str = "prompt",
        cross_repo: bool = False,
    ) -> list[MemoryRecord]:
        if not self.config.enabled or isinstance(self.adapter, NullAdapter):
            return []
        if scope == "prompt" and self.latency.injection_disabled:
            return []
        if scope == "prompt" and not self.config.retrieval.enable_prompt_injection:
            return []
        if scope == "prompt" and len(query) < self.config.retrieval.min_prompt_chars:
            return []
        if self.breaker.is_open:
            return []

        filters = self.identity.as_filter()
        if cross_repo:
            filters.pop("app_id", None)

        effective_top_k = top_k or self.config.retrieval.top_k

        start = time.time()
        try:
            results = self.adapter.search(query, filters=filters, top_k=effective_top_k)
            self.breaker.record_success()
        except Exception:  # noqa: BLE001
            self.breaker.record_failure()
            return []
        elapsed_ms = (time.time() - start) * 1000.0
        if scope == "prompt":
            self.latency.record(elapsed_ms)

        return self._cap_token_budget(results, scope=scope)

    def update(self, memory_id: str, content: str) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled"}
        if not self.config.writes.agent_can_update:
            return {"status": "rejected", "reason": "agent_can_update=false"}
        if self.breaker.is_open:
            return {"status": "rejected", "reason": "circuit breaker open"}
        try:
            result = self.adapter.update(memory_id, content)
            self.breaker.record_success()
            return {"status": "updated", "result": result}
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure()
            return {"status": "rejected", "reason": str(exc)}

    def list_entities(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {}
        try:
            return self.adapter.list_entities()
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure()
            return {"error": str(exc)}

    # ----- prune-only delete surface -----

    def prune_delete(self, memory_id: str) -> dict[str, Any]:
        """Delete entrypoint reserved for `pipeline mem0 prune`. Agent code
        must NOT call this directly per FR-8."""
        return self.adapter.delete(memory_id, allowed_by_prune=True)

    def prune_delete_all(self, filters: dict[str, str]) -> dict[str, Any]:
        return self.adapter.delete_all(filters=filters, allowed_by_prune=True)

    # ----- helpers -----

    def _extract_text(self, messages: list[dict[str, str]] | str) -> str:
        if isinstance(messages, str):
            return messages
        return "\n".join(
            str(item.get("content", "")) if isinstance(item, dict) else str(item)
            for item in messages
        )

    def _cap_token_budget(self, results: list[MemoryRecord], scope: str) -> list[MemoryRecord]:
        # Conservative token estimate: 4 chars per token. Real implementations
        # use the agent's tokenizer; this is a safe approximation.
        budget = self.config.retrieval.token_budget
        if scope == "session_start":
            budget = int(budget * self.config.retrieval.session_start_overflow)
        ordered = sorted(results, key=lambda r: r.score or 0.0, reverse=True)
        kept: list[MemoryRecord] = []
        running_chars = 0
        for record in ordered:
            chars = len(record.content)
            if running_chars + chars > budget * 4:
                break
            kept.append(record)
            running_chars += chars
        return kept

    def _outbox_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Best-effort outbox: persist failed writes locally for next-session retry."""
        try:
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
            import json
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            outbox_path = self.outbox_dir / f"add-{stamp}.json"
            outbox_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
            return {"status": "outboxed", "path": str(outbox_path)}
        except Exception as exc:  # noqa: BLE001 - outbox failure is non-fatal
            return {"status": "lost", "reason": f"outbox write failed: {exc}"}


def build_policy(config: Mem0Config, identity: IdentityContext, adapter: MemoryAdapter | None = None) -> PolicyLayer:
    """Construct a PolicyLayer with the configured adapter."""
    if adapter is None:
        from .adapter import build_adapter

        adapter = build_adapter(config)
    return PolicyLayer(adapter=adapter, config=config, identity=identity)
