# SPDX-License-Identifier: Apache-2.0
"""Agent Pipeline for Claude Code - Mem0 MCP memory layer.

Two-layer architecture per the v2.0 PRD:

  Layer A (file-backed): .agent-runs/<run-id>/memory/*.jsonl
    Unconditional, fast, no network. Written by hooks (Phase 4).
    Source of truth for within-run state and the handoff_current.md
    that SessionStart re-injects.

  Layer B (Mem0): managed Platform OR self-hosted OSS Memory layer.
    Best-effort, behind a circuit breaker. Source of truth for
    across-session knowledge (decisions, anti-patterns, conventions).

Layer A is unconditional. Layer B is opt-in by presence of
`.mem0/config.json` (created by `mem0 init`) and graceful-degrades
when the configured backend is unreachable. Never blocks the agent.
"""

from .config import Mem0Config, load_config
from .identity import IdentityContext, derive_identity
from .policy import PolicyLayer
from .redaction import RedactionResult, scrub

__all__ = [
    "Mem0Config",
    "load_config",
    "IdentityContext",
    "derive_identity",
    "PolicyLayer",
    "RedactionResult",
    "scrub",
]
