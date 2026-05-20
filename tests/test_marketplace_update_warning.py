# SPDX-License-Identifier: Apache-2.0
"""v2.2.2: SessionStart marketplace-update-available warning tests.

Closes the v2.2.1 awareness gap discovered in production: third-party
marketplaces have auto-update OFF by default (per the Claude Code docs
at https://code.claude.com/docs/en/discover-plugins#configure-auto-updates).
A `git pull && git checkout vX.Y.Z` on the marketplace clone followed by
a Cowork restart does NOT install the new version -- Cowork keeps loading
the previously-installed version forever until the operator explicitly
runs `claude plugin install` or toggles auto-update via the `/plugin` UI.

v2.2.1 shipped a cache-hygiene hook that depended on the new version
actually loading. The new version did not load. The operator hit the
auto-update-OFF gotcha and the v2.2.1 feature was a no-op in production
until the operator ran `claude plugin install` manually.

v2.2.2's fix: detect the SHA skew at SessionStart and emit a LOUD
`additionalContext` block with the exact `claude plugin install` command
and the auto-update toggle instructions. The block prepends ahead of
other context so the LLM sees and relays it first.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hooks import hook_runner
from hooks.hook_utils import (
    _read_installed_plugin_sha,
    _read_marketplace_head_sha,
    marketplace_update_available_context,
)


# ---------------------------------------------------------------------------
# Test layout helper
# ---------------------------------------------------------------------------
#
# The real Cowork layout under ~/.claude/plugins/:
#
#   plugins/
#     installed_plugins.json
#     cache/<marketplace>/<plugin>/<version>/  ← loaded plugin lives here
#     marketplaces/<marketplace>/              ← marketplace git clone
#
# This helper builds a synthetic version of that layout under tmp_path
# so the tests don't have to touch the operator's real ~/.claude.


def _make_layout(
    tmp_path: Path,
    *,
    plugin_name: str = "agent-pipeline-claude",
    marketplace_name: str = "agent-pipeline-claude",
    version: str = "2.2.2",
    installed_sha: str | None = "0000000000000000000000000000000000000001",
    head_sha: str | None = "0000000000000000000000000000000000000002",
    create_marketplace_clone: bool = True,
    create_installed_plugins_json: bool = True,
) -> Path:
    """Build a synthetic Cowork layout and return the plugin_root path.

    head_sha controls what `git rev-parse HEAD` returns from the
    marketplace clone (the clone is a real git repo with one commit
    that we manipulate to set the SHA via ``git commit --amend``).

    installed_sha controls what installed_plugins.json records.
    """
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    plugin_root = plugins_root / "cache" / marketplace_name / plugin_name / version
    plugin_root.mkdir(parents=True)
    (plugin_root / "marker.txt").write_text("loaded", encoding="utf-8")
    if create_marketplace_clone and head_sha is not None:
        clone = plugins_root / "marketplaces" / marketplace_name
        clone.mkdir(parents=True)
        # Create a real git repo so `git rev-parse HEAD` succeeds. The
        # SHA we control comes from the empty commit we make below.
        subprocess.run(["git", "init", "--quiet"], cwd=str(clone), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(clone), check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(clone), check=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "synthetic head " + head_sha[:7], "--quiet"],
            cwd=str(clone), check=True,
        )
    if create_installed_plugins_json:
        ip = plugins_root / "installed_plugins.json"
        plugins_entry = {}
        if installed_sha is not None:
            plugins_entry[plugin_name + "@" + marketplace_name] = [
                {
                    "scope": "user",
                    "installPath": str(plugin_root),
                    "version": version,
                    "installedAt": "2026-05-20T00:00:00.000Z",
                    "lastUpdated": "2026-05-20T00:00:00.000Z",
                    "gitCommitSha": installed_sha,
                }
            ]
        ip.write_text(
            json.dumps({"version": 2, "plugins": plugins_entry}, indent=2),
            encoding="utf-8",
        )
    return plugin_root


def _actual_head_sha(plugin_root: Path) -> str:
    """Return the real HEAD SHA of the synthetic marketplace clone."""
    plugins_root = plugin_root.parents[3]
    marketplace_name = plugin_root.parents[1].name
    clone = plugins_root / "marketplaces" / marketplace_name
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(clone), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def test_read_installed_plugin_sha_returns_recorded_sha(tmp_path: Path) -> None:
    plugin_root = _make_layout(tmp_path, installed_sha="abc123def456")
    plugins_root = plugin_root.parents[3]
    sha = _read_installed_plugin_sha(plugins_root, "agent-pipeline-claude", "agent-pipeline-claude")
    assert sha == "abc123def456"


def test_read_installed_plugin_sha_missing_file(tmp_path: Path) -> None:
    sha = _read_installed_plugin_sha(tmp_path, "x", "y")
    assert sha is None


def test_read_installed_plugin_sha_no_matching_entry(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    (plugins_root / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"other@other": [{"gitCommitSha": "x"}]}}),
        encoding="utf-8",
    )
    sha = _read_installed_plugin_sha(plugins_root, "agent-pipeline-claude", "agent-pipeline-claude")
    assert sha is None


def test_read_installed_plugin_sha_malformed_json(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    (plugins_root / "installed_plugins.json").write_text("{not valid json", encoding="utf-8")
    sha = _read_installed_plugin_sha(plugins_root, "x", "y")
    assert sha is None


def test_read_marketplace_head_sha_returns_real_head(tmp_path: Path) -> None:
    plugin_root = _make_layout(tmp_path)
    plugins_root = plugin_root.parents[3]
    clone = plugins_root / "marketplaces" / "agent-pipeline-claude"
    head = _read_marketplace_head_sha(clone)
    assert head is not None
    assert len(head) == 40
    # And it matches what git itself reports
    assert head == _actual_head_sha(plugin_root)


def test_read_marketplace_head_sha_missing_clone(tmp_path: Path) -> None:
    assert _read_marketplace_head_sha(tmp_path / "no-such-clone") is None


def test_read_marketplace_head_sha_not_a_git_repo(tmp_path: Path) -> None:
    fake_clone = tmp_path / "fake"
    fake_clone.mkdir()
    (fake_clone / "README.md").write_text("not a repo", encoding="utf-8")
    assert _read_marketplace_head_sha(fake_clone) is None


# ---------------------------------------------------------------------------
# marketplace_update_available_context
# ---------------------------------------------------------------------------


def test_warning_emitted_when_shas_differ(tmp_path: Path) -> None:
    """Synthetic layout where installed_plugins.json records SHA X but the
    marketplace clone HEAD is Y. The warning must fire."""
    plugin_root = _make_layout(
        tmp_path,
        installed_sha="1111111111111111111111111111111111111111",
    )
    head = _actual_head_sha(plugin_root)
    assert head != "1111111111111111111111111111111111111111"
    warning = marketplace_update_available_context(plugin_root=plugin_root)
    assert warning is not None
    assert "UPDATE AVAILABLE" in warning
    assert "Auto-update is OFF by default" in warning
    assert "claude plugin install agent-pipeline-claude@agent-pipeline-claude" in warning
    # Must include short SHAs of both sides
    assert head[:7] in warning
    assert "1111111" in warning
    # Must include the auto-update toggle path via /plugin UI
    assert "Enable auto-update" in warning
    # Must include the docs reference
    assert "code.claude.com" in warning
    # Must instruct the LLM to surface the warning at the top of its first response
    assert "LLM" in warning or "surface" in warning.lower()


def test_no_warning_when_shas_match(tmp_path: Path) -> None:
    """If installed_plugins.json records the same SHA the marketplace clone
    has at HEAD, no warning -- we're up to date."""
    plugin_root = _make_layout(tmp_path, installed_sha="placeholder")
    actual_head = _actual_head_sha(plugin_root)
    # Re-write installed_plugins.json with the real head SHA
    plugins_root = plugin_root.parents[3]
    ip = plugins_root / "installed_plugins.json"
    data = json.loads(ip.read_text(encoding="utf-8"))
    data["plugins"]["agent-pipeline-claude@agent-pipeline-claude"][0]["gitCommitSha"] = actual_head
    ip.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert marketplace_update_available_context(plugin_root=plugin_root) is None


def test_no_warning_when_installed_plugins_json_missing(tmp_path: Path) -> None:
    plugin_root = _make_layout(tmp_path, create_installed_plugins_json=False)
    assert marketplace_update_available_context(plugin_root=plugin_root) is None


def test_no_warning_when_marketplace_clone_missing(tmp_path: Path) -> None:
    plugin_root = _make_layout(tmp_path, create_marketplace_clone=False)
    assert marketplace_update_available_context(plugin_root=plugin_root) is None


def test_no_warning_when_plugin_name_not_in_installed_plugins(tmp_path: Path) -> None:
    """installed_plugins.json exists but doesn't list this plugin (e.g.
    plugin was uninstalled). Don't warn -- there's nothing to upgrade."""
    plugin_root = _make_layout(tmp_path)
    plugins_root = plugin_root.parents[3]
    ip = plugins_root / "installed_plugins.json"
    ip.write_text(
        json.dumps({"version": 2, "plugins": {"other@other": [{"gitCommitSha": "x"}]}}),
        encoding="utf-8",
    )
    assert marketplace_update_available_context(plugin_root=plugin_root) is None


def test_no_warning_when_plugin_root_layout_unusual(tmp_path: Path) -> None:
    """Dev checkout or test fixture where plugin_root is not at the
    standard cache/<marketplace>/<plugin>/<version>/ depth. Refuse to
    second-guess -- skip silently."""
    weird_root = tmp_path / "agent-pipeline-claude-review"
    weird_root.mkdir()
    assert marketplace_update_available_context(plugin_root=weird_root) is None


def test_no_warning_when_cache_dir_name_not_cache(tmp_path: Path) -> None:
    """Layout sanity check: if the dir at parents[2] is not named
    `cache`, we're not in a standard Cowork install. Skip."""
    # plugin_root parents[2] should be "cache" -- here we put it under "elsewhere"
    weird = tmp_path / "plugins" / "elsewhere" / "agent-pipeline-claude" / "agent-pipeline-claude" / "2.2.2"
    weird.mkdir(parents=True)
    assert marketplace_update_available_context(plugin_root=weird) is None


# ---------------------------------------------------------------------------
# Integration with handle_session_start
# ---------------------------------------------------------------------------


def test_handle_session_start_emits_warning_when_skew_present(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """The warning shows up in the SessionStart additionalContext payload
    when the layout has a SHA skew, even with no active pipeline run."""
    plugin_root = _make_layout(
        tmp_path,
        installed_sha="9999999999999999999999999999999999999999",
    )
    # Point Path(__file__).resolve().parents[1] at our synthetic plugin_root
    # by monkeypatching the helper that the production code reaches for it.
    from hooks import hook_utils

    real_default = hook_utils.marketplace_update_available_context

    def synthetic_default(plugin_root_arg=None):
        return real_default(plugin_root=plugin_root)

    monkeypatch.setattr(hook_runner, "marketplace_update_available_context", synthetic_default)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out, "expected SessionStart to emit a payload when the warning fires"
    payload = json.loads(out)
    additional = payload["hookSpecificOutput"]["additionalContext"]
    assert "UPDATE AVAILABLE" in additional
    assert "claude plugin install agent-pipeline-claude@agent-pipeline-claude" in additional


def test_handle_session_start_no_warning_when_up_to_date(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """No warning + no active run = SessionStart returns 0 with no payload
    (current behavior preserved)."""
    monkeypatch.setattr(
        hook_runner, "marketplace_update_available_context", lambda plugin_root_arg=None: None
    )
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_handle_session_start_warning_appears_before_other_context(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """When both upgrade warning AND active run context exist, the
    warning must appear BEFORE the run context so the LLM reads it first."""
    monkeypatch.setattr(
        hook_runner,
        "marketplace_update_available_context",
        lambda plugin_root_arg=None: "=== TEST UPDATE AVAILABLE ===\nrun the install command",
    )
    # Stage an active run so session_context also fires
    run = tmp_path / ".agent-runs" / "test-run"
    run.mkdir(parents=True)
    (run / "active-control-state.md").write_text(
        "active_run: true\ncurrent_stage: research\nnext_required_action: x\n"
        "stop_condition: n\ncontinuing_to: plan\n",
        encoding="utf-8",
    )
    (run / "manifest.yaml").write_text("pipeline_run:\n  id: x\n  type: feature\n", encoding="utf-8")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    additional = payload["hookSpecificOutput"]["additionalContext"]
    update_idx = additional.find("TEST UPDATE AVAILABLE")
    run_ctx_idx = additional.find("Agent Pipeline active run context")
    assert update_idx != -1, "expected upgrade warning in payload"
    assert run_ctx_idx != -1, "expected active-run context in payload"
    assert update_idx < run_ctx_idx, (
        "upgrade warning must appear before active-run context "
        "(found warning at {0}, run context at {1})".format(update_idx, run_ctx_idx)
    )


def test_handle_session_start_swallows_warning_exceptions(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """If marketplace_update_available_context raises (e.g. unusual
    filesystem error), handle_session_start must not crash."""
    def boom(plugin_root_arg=None):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(hook_runner, "marketplace_update_available_context", boom)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rc = hook_runner.handle_session_start({"cwd": str(tmp_path), "source": "startup"})
    assert rc == 0
