# SPDX-License-Identifier: Apache-2.0
"""Configuration loader for the Mem0 memory layer.

Loads from `.mem0/config.json` in the project root, with environment
variable overrides per PRD FR-1. Returns a `Mem0Config` dataclass
that the rest of the package consumes. Missing or malformed config
returns a `Mem0Config` with `enabled=False` - the agent still gets
file-backed Layer A memory; only Mem0 sync is disabled.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Source the canonical secret/path lists from redaction so config defaults
# can never narrow them. Pre-Pass-6 RedactionConfig hard-coded a shorter
# list (missing AWS access keys and Bearer tokens, and `~/.kube/config`);
# whenever a project had a `.mem0/config.json` without an explicit
# `redaction:` block, `tuple(redaction_raw.get("secret_patterns") or
# RedactionConfig().secret_patterns)` substituted the narrower set and
# real secrets leaked past `scrub()`. The audit (ENG cluster F) flagged
# this as the divergence to fix at the source.
from .redaction import _DEFAULT_BLOCK_PATHS, _DEFAULT_SECRET_PATTERNS


@dataclass(frozen=True)
class LatencyBudget:
    p50_ms: int = 150
    p95_ms: int = 400
    session_start_ms: int = 1500


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = 10
    token_budget: int = 1200
    session_start_overflow: float = 1.5
    enable_prompt_injection: bool = True
    min_prompt_chars: int = 20
    latency_budget: LatencyBudget = field(default_factory=LatencyBudget)


@dataclass(frozen=True)
class WriteConfig:
    allowed_types: tuple[str, ...] = (
        "decision",
        "task_learning",
        "anti_pattern",
        "user_preference",
        "environmental",
        "convention",
        "session_state",
    )
    agent_can_delete: bool = False
    agent_can_update: bool = True


@dataclass(frozen=True)
class RedactionConfig:
    # Defaults derive from the canonical lists in memory/redaction.py so
    # the config layer cannot silently narrow what `scrub()` would catch.
    # Override via .mem0/config.json (an explicit `redaction.secret_patterns:
    # []` still wins — empty-list opt-out is a deliberate operator choice).
    secret_patterns: tuple[str, ...] = _DEFAULT_SECRET_PATTERNS
    block_paths: tuple[str, ...] = _DEFAULT_BLOCK_PATHS


@dataclass(frozen=True)
class HygieneConfig:
    prune_run_id_after_days: int = 7
    prune_session_state_after_days: int = 30
    review_long_lived_after_days: int = 180


@dataclass(frozen=True)
class CircuitBreakerConfig:
    consecutive_failures: int = 5
    open_seconds: int = 300


@dataclass(frozen=True)
class ConsentConfig:
    platform_requires_consent: bool = True
    consent_file: str = ".mem0/consent.json"


@dataclass(frozen=True)
class PlatformConfig:
    api_key_env: str = "MEM0_API_KEY"
    endpoint: str = "https://api.mem0.ai"
    mcp_endpoint: str = "https://mcp.mem0.ai/mcp/"


@dataclass(frozen=True)
class OssConfig:
    # 8888 is the vendor docker-compose external port that maps to the
    # FastAPI server's internal 8000. The Next.js dashboard runs on 3000.
    # The mem0ai SDK's Memory(base_url=...) wants the API, NOT the dashboard.
    base_url: str = "http://localhost:8888"
    compose_dir: str = "./vendor/mem0/server"
    admin_api_key_env: str = "MEM0_ADMIN_API_KEY"


@dataclass(frozen=True)
class IdentityConfig:
    user_id_strategy: str = "hash_git_email"
    agent_id: str = "claude-code"
    app_id_strategy: str = "slug_git_remote"
    run_id_strategy: str = "branch_sha_epoch"


@dataclass(frozen=True)
class Mem0Config:
    enabled: bool
    mode: str  # "platform" | "oss"
    platform: PlatformConfig = field(default_factory=PlatformConfig)
    oss: OssConfig = field(default_factory=OssConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    writes: WriteConfig = field(default_factory=WriteConfig)
    redaction: RedactionConfig = field(default_factory=RedactionConfig)
    hygiene: HygieneConfig = field(default_factory=HygieneConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    consent: ConsentConfig = field(default_factory=ConsentConfig)
    project: str = "default"

    @property
    def consent_granted_for(self) -> bool:
        """True iff platform mode + consent file exists with grant=true."""
        if self.mode != "platform":
            return True
        consent_path = Path(self.consent.consent_file).expanduser()
        if not consent_path.exists():
            return False
        try:
            data = json.loads(consent_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return data.get("grant") is True


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


def load_config(project_root: Path | None = None) -> Mem0Config:
    """Load Mem0 config from `<project>/.mem0/config.json` with env overrides.

    Returns Mem0Config(enabled=False) when no config exists. This preserves
    the file-backed Layer A path: hooks still write memory locally; only
    the Mem0 sync is inert.
    """
    root = project_root or Path.cwd()
    config_path = root / ".mem0" / "config.json"
    if not config_path.exists():
        # Env-only opt-in path: MEM0_MODE=platform with MEM0_API_KEY set
        # enables platform without a config file (PRD FR-1 fallback).
        env_mode = _env("MEM0_MODE", "oss")
        if env_mode == "platform" and _env("MEM0_API_KEY"):
            return Mem0Config(enabled=True, mode="platform")
        if env_mode == "oss" and _env("MEM0_BASE_URL"):
            return Mem0Config(enabled=True, mode="oss",
                              oss=OssConfig(base_url=_env("MEM0_BASE_URL", "http://localhost:8888")))
        return Mem0Config(enabled=False, mode=env_mode or "oss")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Mem0Config(enabled=False, mode="oss")

    mode = str(raw.get("mode", "oss"))
    platform_raw = raw.get("platform") or {}
    oss_raw = raw.get("oss") or {}
    identity_raw = raw.get("identity") or {}
    retrieval_raw = raw.get("retrieval") or {}
    writes_raw = raw.get("writes") or {}
    redaction_raw = raw.get("redaction") or {}
    hygiene_raw = raw.get("hygiene") or {}
    consent_raw = raw.get("consent") or {}
    breaker_raw = raw.get("circuit_breaker") or {}
    latency_raw = (retrieval_raw.get("latency_budget_ms") or {}) if isinstance(retrieval_raw, dict) else {}

    latency = LatencyBudget(
        p50_ms=int(latency_raw.get("p50", 150)),
        p95_ms=int(latency_raw.get("p95", 400)),
        session_start_ms=int(latency_raw.get("session_start", 1500)),
    )
    retrieval = RetrievalConfig(
        top_k=int(retrieval_raw.get("top_k", 10)),
        token_budget=int(retrieval_raw.get("token_budget", 1200)),
        session_start_overflow=float(retrieval_raw.get("session_start_overflow", 1.5)),
        enable_prompt_injection=bool(retrieval_raw.get("enable_prompt_injection", True)),
        min_prompt_chars=int(retrieval_raw.get("min_prompt_chars", 20)),
        latency_budget=latency,
    )
    writes = WriteConfig(
        allowed_types=tuple(writes_raw.get("allowed_types") or WriteConfig.__dataclass_fields__["allowed_types"].default),
        agent_can_delete=bool(writes_raw.get("agent_can_delete", False)),
        agent_can_update=bool(writes_raw.get("agent_can_update", True)),
    )
    redaction = RedactionConfig(
        secret_patterns=tuple(redaction_raw.get("secret_patterns") or RedactionConfig().secret_patterns),
        block_paths=tuple(redaction_raw.get("block_paths") or RedactionConfig().block_paths),
    )
    hygiene = HygieneConfig(
        prune_run_id_after_days=int(hygiene_raw.get("prune_run_id_after_days", 7)),
        prune_session_state_after_days=int(hygiene_raw.get("prune_session_state_after_days", 30)),
        review_long_lived_after_days=int(hygiene_raw.get("review_long_lived_after_days", 180)),
    )
    consent = ConsentConfig(
        platform_requires_consent=bool(consent_raw.get("platform_requires_consent", True)),
        consent_file=str(consent_raw.get("consent_file", ".mem0/consent.json")),
    )
    breaker = CircuitBreakerConfig(
        consecutive_failures=int(breaker_raw.get("consecutive_failures", 5)),
        open_seconds=int(breaker_raw.get("open_seconds", 300)),
    )
    platform = PlatformConfig(
        api_key_env=str(platform_raw.get("api_key_env", "MEM0_API_KEY")),
        endpoint=str(platform_raw.get("endpoint", "https://api.mem0.ai")),
        mcp_endpoint=str(platform_raw.get("mcp_endpoint", "https://mcp.mem0.ai/mcp/")),
    )
    oss = OssConfig(
        base_url=str(oss_raw.get("base_url", "http://localhost:8888")),
        compose_dir=str(oss_raw.get("compose_dir", "./vendor/mem0/server")),
        admin_api_key_env=str(oss_raw.get("admin_api_key_env", "MEM0_ADMIN_API_KEY")),
    )
    identity = IdentityConfig(
        user_id_strategy=str(identity_raw.get("user_id_strategy", "hash_git_email")),
        agent_id=str(identity_raw.get("agent_id", "claude-code")),
        app_id_strategy=str(identity_raw.get("app_id_strategy", "slug_git_remote")),
        run_id_strategy=str(identity_raw.get("run_id_strategy", "branch_sha_epoch")),
    )

    return Mem0Config(
        enabled=True,
        mode=mode,
        platform=platform,
        oss=oss,
        identity=identity,
        retrieval=retrieval,
        writes=writes,
        redaction=redaction,
        hygiene=hygiene,
        circuit_breaker=breaker,
        consent=consent,
        project=str(raw.get("project", "default")),
    )
