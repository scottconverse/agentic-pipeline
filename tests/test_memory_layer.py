# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the v2.0 memory/ package.

Covers PRD FR-1 (config), FR-6 (identity scoping), FR-7 (metadata
taxonomy enforcement), FR-8 (agent cannot delete), FR-11 (redaction),
FR-13 (circuit breaker), FR-14 (consent gate). Uses a mock adapter so
no Mem0 SDK is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from memory.adapter import MemoryAdapter, MemoryRecord, NullAdapter, build_adapter
from memory.config import Mem0Config, load_config
from memory.identity import IdentityContext, derive_identity
from memory.policy import PolicyLayer
from memory.redaction import scrub


# ---------------------------------------------------------------------------
# Mock adapter for policy tests
# ---------------------------------------------------------------------------


class MockAdapter(MemoryAdapter):
    def __init__(self, search_results=None, fail_count: int = 0, raise_on_get: bool = False):
        self.adds: list[dict[str, Any]] = []
        self.searches: list[dict[str, Any]] = []
        self.updates: list[tuple[str, str]] = []
        self.list_entities_calls = 0
        self._search_results = search_results or []
        self._fail_count = fail_count
        self._raise_on_get = raise_on_get

    def add(self, messages, *, metadata, filters):
        if self._fail_count > 0:
            self._fail_count -= 1
            raise RuntimeError("simulated adapter failure")
        record = {"messages": messages, "metadata": dict(metadata), "filters": dict(filters)}
        self.adds.append(record)
        return {"id": f"mock-{len(self.adds)}"}

    def search(self, query, *, filters, top_k=10):
        self.searches.append({"query": query, "filters": dict(filters), "top_k": top_k})
        return list(self._search_results)

    def get(self, memory_id):
        if self._raise_on_get:
            raise RuntimeError("simulated get failure")
        return MemoryRecord(id=memory_id, content="mock content")

    def get_all(self, *, filters):
        return []

    def update(self, memory_id, content):
        self.updates.append((memory_id, content))
        return {"id": memory_id}

    def list_entities(self):
        self.list_entities_calls += 1
        return {"users": ["u1"], "agents": ["claude-code"], "apps": ["a1"]}

    def _delete(self, memory_id):
        return {"id": memory_id, "deleted": True}

    def _delete_all(self, filters):
        return {"deleted_all": True}

    def _delete_entities(self):
        return {"deleted_entities": True}


def _fake_identity(tmp_path: Path) -> IdentityContext:
    return IdentityContext(
        user_id="u-test",
        agent_id="claude-code",
        app_id="app-test",
        run_id="branch-abc1234-1700000000",
        branch="main",
        repo_root=tmp_path,
    )


def _enabled_config() -> Mem0Config:
    return Mem0Config(enabled=True, mode="oss")


# ---------------------------------------------------------------------------
# FR-1: config loading
# ---------------------------------------------------------------------------


def test_load_config_returns_disabled_when_no_file_and_no_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MEM0_MODE", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_BASE_URL", raising=False)

    cfg = load_config(tmp_path)

    assert cfg.enabled is False


def test_load_config_reads_json_file(tmp_path) -> None:
    cfg_dir = tmp_path / ".mem0"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        '{"mode": "platform", "project": "test-proj", "writes": {"agent_can_update": false}}',
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)

    assert cfg.enabled is True
    assert cfg.mode == "platform"
    assert cfg.project == "test-proj"
    assert cfg.writes.agent_can_update is False


def test_load_config_env_only_platform_opt_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_MODE", "platform")
    monkeypatch.setenv("MEM0_API_KEY", "sk-test-key")

    cfg = load_config(tmp_path)

    assert cfg.enabled is True
    assert cfg.mode == "platform"


# ---------------------------------------------------------------------------
# FR-6: identity scoping
# ---------------------------------------------------------------------------


def test_identity_derive_uses_git_email_hash_and_repo_slug(tmp_path) -> None:
    # derive_identity uses git config; subprocess fallback for empty results.
    # We can't easily seed a git config from pytest, so just assert shape.
    identity = derive_identity(tmp_path)

    assert len(identity.user_id) == 16
    assert all(c in "0123456789abcdef" for c in identity.user_id)
    assert identity.agent_id == "claude-code"
    assert identity.app_id  # non-empty fallback to dir name
    assert "-" in identity.run_id  # branch-sha-epoch shape


def test_identity_filter_defaults_to_user_and_app(tmp_path) -> None:
    identity = _fake_identity(tmp_path)

    assert identity.as_filter() == {"user_id": "u-test", "app_id": "app-test"}


def test_identity_write_keys_include_run_id(tmp_path) -> None:
    identity = _fake_identity(tmp_path)

    keys = identity.as_write_keys(include_run=True)

    assert keys == {
        "user_id": "u-test",
        "agent_id": "claude-code",
        "app_id": "app-test",
        "run_id": "branch-abc1234-1700000000",
    }


def test_identity_write_keys_can_omit_run(tmp_path) -> None:
    identity = _fake_identity(tmp_path)

    keys = identity.as_write_keys(include_run=False)

    assert "run_id" not in keys


# ---------------------------------------------------------------------------
# FR-7: metadata taxonomy enforcement
# ---------------------------------------------------------------------------


def test_policy_rejects_add_with_invalid_type(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    result = policy.add("hello", metadata={"type": "not_in_allowed_set"})

    assert result["status"] == "rejected"
    assert "allowed_types" in result["reason"]
    assert adapter.adds == []


def test_policy_accepts_add_with_known_type(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    result = policy.add(
        "user prefers explicit error types over exceptions",
        metadata={"type": "user_preference"},
    )

    assert result["status"] == "added"
    assert len(adapter.adds) == 1
    assert adapter.adds[0]["filters"]["user_id"] == "u-test"
    assert adapter.adds[0]["filters"]["app_id"] == "app-test"


# ---------------------------------------------------------------------------
# FR-8: agent cannot delete
# ---------------------------------------------------------------------------


def test_adapter_delete_requires_allowed_by_prune(tmp_path) -> None:
    adapter = MockAdapter()

    with pytest.raises(PermissionError):
        adapter.delete("any-id")

    # prune path works
    assert adapter.delete("any-id", allowed_by_prune=True) == {"id": "any-id", "deleted": True}


def test_policy_prune_delete_uses_allowed_by_prune(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    result = policy.prune_delete("mem-99")

    assert result == {"id": "mem-99", "deleted": True}


# ---------------------------------------------------------------------------
# FR-11: redaction
# ---------------------------------------------------------------------------


def test_redaction_blocks_secret_pattern() -> None:
    result = scrub("Found a token: sk-abcdefghijklmnopqrstuvwxyz1234567890")

    assert result.allowed is False
    assert any("sk" in pat for pat in result.matched_patterns)


def test_redaction_blocks_block_path() -> None:
    result = scrub("Reading ~/.ssh/id_rsa to grab the key")

    assert result.allowed is False
    assert "~/.ssh" in result.matched_paths


def test_redaction_passes_clean_text() -> None:
    result = scrub("The user prefers JWT auth over session cookies.")

    assert result.allowed is True
    assert result.matched_patterns == ()
    assert result.matched_paths == ()


def test_policy_rejects_add_with_redacted_content(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    result = policy.add(
        "We hit an error: token sk-abcdefghijklmnopqrstuvwxyz1234567890 was bad",
        metadata={"type": "anti_pattern"},
    )

    assert result["status"] == "rejected"
    assert "redaction" in result["reason"]
    assert adapter.adds == []


# ---------------------------------------------------------------------------
# FR-13: circuit breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_after_5_consecutive_failures(tmp_path) -> None:
    adapter = MockAdapter(fail_count=10)
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    results = [policy.add("msg", metadata={"type": "decision"}) for _ in range(7)]

    # First 4 fail and outbox; on the 5th the breaker opens. The 6th and 7th
    # also outbox because the breaker is now open.
    assert all(r["status"] in ("outboxed", "lost") for r in results)
    assert policy.breaker.is_open is True


def test_circuit_breaker_resets_on_success(tmp_path) -> None:
    adapter = MockAdapter(fail_count=2)
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    r1 = policy.add("fail one", metadata={"type": "decision"})
    r2 = policy.add("fail two", metadata={"type": "decision"})
    r3 = policy.add("success", metadata={"type": "decision"})

    assert r1["status"] == "outboxed"
    assert r2["status"] == "outboxed"
    assert r3["status"] == "added"
    assert policy.breaker.is_open is False


# ---------------------------------------------------------------------------
# FR-14: consent gate
# ---------------------------------------------------------------------------


def test_consent_gate_blocks_platform_writes_without_consent(tmp_path) -> None:
    adapter = MockAdapter()
    config = Mem0Config(enabled=True, mode="platform")
    policy = PolicyLayer(adapter, config, _fake_identity(tmp_path))

    result = policy.add("test", metadata={"type": "decision"})

    assert result["status"] == "rejected"
    assert "consent" in result["reason"].lower()


def test_consent_gate_allows_platform_with_grant(tmp_path) -> None:
    adapter = MockAdapter()
    consent_path = tmp_path / ".mem0" / "consent.json"
    consent_path.parent.mkdir(parents=True)
    consent_path.write_text('{"grant": true}', encoding="utf-8")
    # consent_file is relative; Mem0Config.consent_granted_for resolves via
    # Path(...).expanduser() — for a relative path, that's relative to cwd.
    # Use absolute path in the config to match what the test wrote.
    from memory.config import ConsentConfig
    config = Mem0Config(
        enabled=True,
        mode="platform",
        consent=ConsentConfig(platform_requires_consent=True, consent_file=str(consent_path)),
    )
    policy = PolicyLayer(adapter, config, _fake_identity(tmp_path))

    result = policy.add("test", metadata={"type": "decision"})

    assert result["status"] == "added"


def test_consent_gate_irrelevant_for_oss(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, Mem0Config(enabled=True, mode="oss"), _fake_identity(tmp_path))

    result = policy.add("test", metadata={"type": "decision"})

    assert result["status"] == "added"


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------


def test_build_adapter_returns_null_when_disabled() -> None:
    adapter = build_adapter(Mem0Config(enabled=False, mode="oss"))

    assert isinstance(adapter, NullAdapter)


def test_build_adapter_returns_null_when_platform_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    adapter = build_adapter(Mem0Config(enabled=True, mode="platform"))

    assert isinstance(adapter, NullAdapter)


# ---------------------------------------------------------------------------
# Search and update
# ---------------------------------------------------------------------------


def test_search_uses_user_and_app_filter_by_default(tmp_path) -> None:
    adapter = MockAdapter(search_results=[
        MemoryRecord(id="m1", content="prior decision: use JWT", score=0.9),
    ])
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    results = policy.search("How did we handle auth?", scope="prompt")

    assert len(adapter.searches) == 1
    assert adapter.searches[0]["filters"] == {"user_id": "u-test", "app_id": "app-test"}
    assert len(results) == 1
    assert results[0].content == "prior decision: use JWT"


def test_search_can_broaden_cross_repo(tmp_path) -> None:
    adapter = MockAdapter(search_results=[])
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    policy.search("How did we handle auth?", scope="prompt", cross_repo=True)

    assert adapter.searches[0]["filters"] == {"user_id": "u-test"}


def test_search_skips_short_prompts(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    results = policy.search("hi", scope="prompt")

    assert results == []
    assert adapter.searches == []


def test_update_respects_agent_can_update_flag(tmp_path) -> None:
    adapter = MockAdapter()
    from memory.config import WriteConfig
    config = Mem0Config(enabled=True, mode="oss", writes=WriteConfig(agent_can_update=False))
    policy = PolicyLayer(adapter, config, _fake_identity(tmp_path))

    result = policy.update("mem-1", "revised content")

    assert result["status"] == "rejected"
    assert adapter.updates == []


def test_update_succeeds_when_allowed(tmp_path) -> None:
    adapter = MockAdapter()
    policy = PolicyLayer(adapter, _enabled_config(), _fake_identity(tmp_path))

    result = policy.update("mem-1", "revised content")

    assert result["status"] == "updated"
    assert adapter.updates == [("mem-1", "revised content")]


# ---------------------------------------------------------------------------
# Prune --execute behavior (Layer A archive + Layer B delete)
# ---------------------------------------------------------------------------


def test_prune_dry_run_does_not_archive(tmp_path, monkeypatch) -> None:
    """Dry-run lists candidates but never touches files or backend."""
    import sys
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    runs_root = tmp_path / ".agent-runs"
    runs_root.mkdir()
    aged_run = runs_root / "old-run"
    aged_run.mkdir()
    (aged_run / "marker.md").write_text("x", encoding="utf-8")
    # Backdate the directory by 10 days
    old_time = time.time() - (10 * 86400)
    import os
    os.utime(aged_run, (old_time, old_time))

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    import mem0_bootstrap
    import argparse
    args = argparse.Namespace(execute=False, yes=False)

    rc = mem0_bootstrap.cmd_prune(args)

    assert rc == 0
    assert aged_run.exists(), "dry-run must not touch any files"
    assert not (runs_root / "_archived").exists()


def test_prune_execute_archives_aged_layer_a_dirs(tmp_path, monkeypatch) -> None:
    """--execute --yes moves aged run dirs to .agent-runs/_archived/."""
    import sys
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    runs_root = tmp_path / ".agent-runs"
    runs_root.mkdir()
    aged_run = runs_root / "old-run"
    aged_run.mkdir()
    (aged_run / "marker.md").write_text("x", encoding="utf-8")
    fresh_run = runs_root / "fresh-run"
    fresh_run.mkdir()
    old_time = time.time() - (30 * 86400)
    import os
    os.utime(aged_run, (old_time, old_time))

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    import mem0_bootstrap
    import argparse
    args = argparse.Namespace(execute=True, yes=True)

    rc = mem0_bootstrap.cmd_prune(args)

    assert rc == 0
    assert not aged_run.exists(), "aged run must be moved out"
    assert fresh_run.exists(), "fresh run must be preserved"
    archive_root = runs_root / "_archived"
    assert archive_root.exists()
    archived = list(archive_root.iterdir())
    assert len(archived) == 1
    assert archived[0].name.startswith("old-run-")


def test_mem0_init_finds_template_in_dot_pipelines(tmp_path, monkeypatch, capsys) -> None:
    """Phase 6.c bug fix (checkpoint C): mem0 init must find the template
    at .pipelines/ (where pipeline-init scaffolds it), not only at
    pipelines/. This test mirrors the actual operator workflow."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    (tmp_path / ".pipelines").mkdir()
    template = {
        "mode": "oss",
        "project": "smoke",
        "platform": {"api_key_env": "MEM0_API_KEY", "endpoint": "https://api.mem0.ai", "mcp_endpoint": "x"},
        "oss": {"base_url": "http://localhost:3000", "compose_dir": "./vendor/mem0/server", "admin_api_key_env": "MEM0_ADMIN_API_KEY"},
        "identity": {"user_id_strategy": "hash_git_email", "agent_id": "claude-code", "app_id_strategy": "slug_git_remote", "run_id_strategy": "branch_sha_epoch"},
        "retrieval": {"top_k": 10, "token_budget": 1200, "session_start_overflow": 1.5, "enable_prompt_injection": True, "min_prompt_chars": 20, "latency_budget_ms": {"p50": 150, "p95": 400, "session_start": 1500}},
        "writes": {"allowed_types": ["decision"], "agent_can_delete": False, "agent_can_update": True},
        "redaction": {"secret_patterns": [], "block_paths": []},
        "hygiene": {"prune_run_id_after_days": 7, "prune_session_state_after_days": 30, "review_long_lived_after_days": 180},
        "circuit_breaker": {"consecutive_failures": 5, "open_seconds": 300},
        "consent": {"platform_requires_consent": True, "consent_file": ".mem0/consent.json"},
    }
    import json
    (tmp_path / ".pipelines" / "mem0-config-template.json").write_text(
        json.dumps(template, indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    import mem0_bootstrap
    import argparse
    args = argparse.Namespace(mode="oss", force=False)

    rc = mem0_bootstrap.cmd_init(args)

    assert rc == 0
    assert (tmp_path / ".mem0" / "config.json").exists()


def test_mem0_init_missing_template_returns_clear_error(tmp_path, monkeypatch, capsys) -> None:
    """When no template exists in any of the three search locations,
    cmd_init returns 2 with a clear error naming the search paths."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Stub out the plugin-install-dir fallback so the third location also misses.
    # We do this by pointing the source root at an empty tmp.
    import mem0_bootstrap
    real_locate = mem0_bootstrap._locate_config_template

    def stub_locate(project_root):
        # Search only .pipelines/ and pipelines/, skip plugin fallback
        for sub in (".pipelines", "pipelines"):
            candidate = project_root / sub / "mem0-config-template.json"
            if candidate.exists():
                return candidate
        return None

    monkeypatch.setattr(mem0_bootstrap, "_locate_config_template", stub_locate)

    import argparse
    args = argparse.Namespace(mode="oss", force=False)

    rc = mem0_bootstrap.cmd_init(args)

    assert rc == 2


def test_prune_execute_without_yes_refuses(tmp_path, monkeypatch) -> None:
    """--execute without --yes refuses to act (FR-12 non-interactive token)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    runs_root = tmp_path / ".agent-runs"
    runs_root.mkdir()

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    import mem0_bootstrap
    import argparse
    args = argparse.Namespace(execute=True, yes=False)

    rc = mem0_bootstrap.cmd_prune(args)

    assert rc == 2
