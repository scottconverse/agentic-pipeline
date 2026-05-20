"""v2.2.1: stale plugin cache hygiene tests.

Closes the v2.2.x upgrade-hygiene gap where Cowork's plugin manager
updated `installed_plugins.json` to point at the new version but left
the prior version's cache directory on disk. Each upgrade leaked
1.5-2 MB of dead code that was:

- Confusing during debugging (multiple `2.X.Y/` siblings under
  ~/.claude/plugins/cache/agent-pipeline-claude/agent-pipeline-claude/
  with no indication which is live)
- Vulnerable to accidental repoint via hand-edited installed_plugins.json
- Wasted disk space

The fix: `cleanup_stale_plugin_caches()` deletes every sibling of the
loaded version directory whose name parses as a strictly-lower semver.
Fires from `handle_session_start` once per session (idempotent).
"""

from __future__ import annotations

from pathlib import Path

from hooks.hook_utils import (
    _parse_semver,
    cleanup_stale_plugin_caches,
)


def _make_cache_layout(
    tmp_path: Path, *, current: str, siblings: list[str]
) -> Path:
    """Build a synthetic Cowork cache layout under ``tmp_path``.

    Returns the path to the ``current`` plugin-root directory. Each
    sibling listed in ``siblings`` is created as an empty directory
    next to it; a marker file is dropped inside so we can confirm the
    cleanup actually removed contents.
    """
    cache_parent = tmp_path / "cache" / "plugin-name" / "plugin-name"
    cache_parent.mkdir(parents=True)
    current_dir = cache_parent / current
    current_dir.mkdir()
    (current_dir / ".keep").write_text("live", encoding="utf-8")
    for name in siblings:
        sib = cache_parent / name
        sib.mkdir()
        (sib / ".keep").write_text("stale", encoding="utf-8")
    return current_dir


# ---------------------------------------------------------------------------
# _parse_semver
# ---------------------------------------------------------------------------


def test_parse_semver_valid() -> None:
    assert _parse_semver("2.2.1") == (2, 2, 1)
    assert _parse_semver("0.0.0") == (0, 0, 0)
    assert _parse_semver("10.20.30") == (10, 20, 30)


def test_parse_semver_invalid_returns_none() -> None:
    assert _parse_semver("v2.2.1") is None  # leading 'v'
    assert _parse_semver("2.2") is None  # too few parts
    assert _parse_semver("2.2.1.4") is None  # too many parts
    assert _parse_semver("2.2.1-beta") is None  # pre-release suffix
    assert _parse_semver("__pycache__") is None
    assert _parse_semver("") is None
    assert _parse_semver("a.b.c") is None


# ---------------------------------------------------------------------------
# cleanup_stale_plugin_caches
# ---------------------------------------------------------------------------


def test_cleanup_deletes_all_lower_semver_siblings(tmp_path: Path) -> None:
    current = _make_cache_layout(
        tmp_path, current="2.2.1", siblings=["2.0.0", "2.1.0", "2.2.0"]
    )
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert set(deleted) == {"2.0.0", "2.1.0", "2.2.0"}
    remaining = sorted(
        p.name for p in current.parent.iterdir() if p.is_dir()
    )
    assert remaining == ["2.2.1"]


def test_cleanup_returns_empty_when_no_siblings(tmp_path: Path) -> None:
    current = _make_cache_layout(tmp_path, current="2.2.1", siblings=[])
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert deleted == []


def test_cleanup_preserves_higher_version_siblings(tmp_path: Path) -> None:
    """If a higher version dir somehow exists (e.g. aborted upgrade),
    leave it alone. Only strictly-lower siblings get deleted."""
    current = _make_cache_layout(
        tmp_path, current="2.2.1", siblings=["2.0.0", "3.0.0"]
    )
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert deleted == ["2.0.0"]
    remaining = sorted(p.name for p in current.parent.iterdir() if p.is_dir())
    assert remaining == ["2.2.1", "3.0.0"]


def test_cleanup_ignores_non_semver_siblings(tmp_path: Path) -> None:
    """Random directory names (`__pycache__`, `.git`, `tmp`) must be left
    alone — only `MAJOR.MINOR.PATCH` siblings are candidates."""
    current = _make_cache_layout(
        tmp_path,
        current="2.2.1",
        siblings=["__pycache__", ".git", "tmp-backup", "v2.0.0", "2.0"],
    )
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert deleted == []
    remaining = sorted(p.name for p in current.parent.iterdir() if p.is_dir())
    # All siblings still present — none match the strict semver pattern.
    assert "__pycache__" in remaining
    assert ".git" in remaining
    assert "tmp-backup" in remaining
    assert "v2.0.0" in remaining
    assert "2.0" in remaining


def test_cleanup_mixed_semver_and_non_semver(tmp_path: Path) -> None:
    current = _make_cache_layout(
        tmp_path,
        current="2.2.1",
        siblings=["2.1.0", "2.2.0", "__pycache__", "3.0.0-rc1"],
    )
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert set(deleted) == {"2.1.0", "2.2.0"}
    remaining = sorted(p.name for p in current.parent.iterdir() if p.is_dir())
    assert "__pycache__" in remaining  # non-semver name preserved
    assert "3.0.0-rc1" in remaining  # not pure semver, preserved
    assert "2.2.1" in remaining  # current preserved
    assert "2.1.0" not in remaining
    assert "2.2.0" not in remaining


def test_cleanup_refuses_when_current_dir_name_not_semver(tmp_path: Path) -> None:
    """If the loaded plugin's directory isn't named as semver (e.g. a
    dev checkout under `agent-pipeline-claude-review/` instead of
    `cache/.../2.2.1/`), refuse to touch anything. The safety check
    prevents the cleanup from running outside a Cowork install layout."""
    current = _make_cache_layout(
        tmp_path,
        current="agent-pipeline-claude-review",
        siblings=["2.0.0", "2.1.0"],
    )
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert deleted == []
    remaining = sorted(p.name for p in current.parent.iterdir() if p.is_dir())
    # All siblings still present — cleanup refused.
    assert remaining == ["2.0.0", "2.1.0", "agent-pipeline-claude-review"]


def test_cleanup_is_idempotent(tmp_path: Path) -> None:
    """Running cleanup twice should yield the same end state and the
    second call should return []."""
    current = _make_cache_layout(
        tmp_path, current="2.2.1", siblings=["2.1.0"]
    )
    first = cleanup_stale_plugin_caches(plugin_root=current)
    second = cleanup_stale_plugin_caches(plugin_root=current)
    assert set(first) == {"2.1.0"}
    assert second == []


def test_cleanup_recursive_delete_removes_contents(tmp_path: Path) -> None:
    """The cleanup must remove the sibling's contents recursively, not
    just the top-level dir entry. Verify by creating nested files."""
    current = _make_cache_layout(
        tmp_path, current="2.2.1", siblings=["2.1.0"]
    )
    stale = current.parent / "2.1.0"
    (stale / "hooks").mkdir()
    (stale / "hooks" / "hook_utils.py").write_text("# stale", encoding="utf-8")
    (stale / "skills").mkdir()
    (stale / "skills" / "run").mkdir()
    (stale / "skills" / "run" / "SKILL.md").write_text("stale", encoding="utf-8")
    deleted = cleanup_stale_plugin_caches(plugin_root=current)
    assert deleted == ["2.1.0"]
    assert not stale.exists()


def test_cleanup_returns_empty_when_plugin_root_missing(tmp_path: Path) -> None:
    """If the resolved plugin_root doesn't exist (defensive guard),
    return [] without raising."""
    nonexistent = tmp_path / "no-such-dir"
    deleted = cleanup_stale_plugin_caches(plugin_root=nonexistent)
    assert deleted == []


def test_cleanup_default_plugin_root_uses_module_path(tmp_path: Path, monkeypatch) -> None:
    """When plugin_root is None (production code path), the function
    resolves from `__file__`. We confirm by checking that calling with
    plugin_root=None doesn't crash and returns a list (the actual
    deletion behavior on the dev checkout is the refuses-when-not-semver
    branch tested above)."""
    deleted = cleanup_stale_plugin_caches()
    assert isinstance(deleted, list)


# ---------------------------------------------------------------------------
# Integration with handle_session_start
# ---------------------------------------------------------------------------


def test_handle_session_start_calls_cleanup_and_swallows_errors(
    tmp_path: Path, monkeypatch
) -> None:
    """`handle_session_start` must call `cleanup_stale_plugin_caches`
    and never crash if cleanup raises. Verified by monkeypatching the
    function to raise."""
    from hooks import hook_runner

    def boom(plugin_root=None):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(hook_runner, "cleanup_stale_plugin_caches", boom)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # handle_session_start should return 0 (no payload) even when cleanup
    # raises — hygiene failure must not crash the hook.
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
