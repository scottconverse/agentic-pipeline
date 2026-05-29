#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Agent Pipeline Claude Code lifecycle hooks.

Ported from agent-pipeline-codex v0.9.0 (hooks/hook_utils.py).
Adapted for claude:
- STALE_STANDALONE_SKILLS lists claude's namespaced skill surface
- NAMESPACED_PREFIX is `agent-pipeline-claude:`
- adds memory-file routing for 5 additional Cowork event types
  (PostToolUseFailure, PreCompact, PostCompact, SubagentStop, SessionEnd)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Claude's namespaced skill surface. If a user prompt names one of these
# bare (without the `agent-pipeline-claude:` prefix), the hook nudges
# them toward the namespaced form so they don't accidentally trigger a
# stale standalone skill of the same name installed under ~/.claude/.
STALE_STANDALONE_SKILLS = {
    "audit-init",
    "intake",
    "pipeline-init",
    "run",
    "show-run-status",
    "grant-autonomous",
    "run-autonomous",
}
NAMESPACED_PREFIX = "agent-pipeline-claude:"
MAX_MEMORY_TEXT = 1200
MAX_HANDOFF_RECORDS = 8

DESTRUCTIVE_PATTERNS = (
    r"\brm\s+-[^\n;|&]*r[^\n;|&]*f\b",
    r"\bRemove-Item\b[^\n;|&]*\b-Recurse\b[^\n;|&]*\b-Force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\b[^\n;|&]*\s--force(?:-with-lease)?\b",
    r"\bnpm\s+publish\b",
    r"\b(drop\s+database|drop\s+table|truncate\s+table)\b",
    r"\bdocker\s+push\b",
    r"\bkubectl\s+(apply|delete|replace)\b",
)
EXTERNAL_OR_RELEASE_PATTERNS = (
    r"\bgit\s+push\b",
    r"\bgh\s+pr\s+(create|merge)\b",
    r"\bgh\s+release\b",
    r"\bcurl\b[^\n;|&]*\s-X\s+(POST|PUT|PATCH|DELETE)\b",
    r"\bInvoke-WebRequest\b[^\n;|&]*\b-Method\s+(Post|Put|Patch|Delete)\b",
)
DEPENDENCY_PATTERNS = (
    r"\bnpm\s+install\b",
    r"\bpip\s+install\b",
    r"\buv\s+add\b",
    r"\bpoetry\s+add\b",
)
SECRET_PATTERNS = (
    r"(?<![\w])(?-i:[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*)\s*=",
    r"\b(cat|type|Get-Content)\b[^\n;|&]*(id_rsa|\.env|credentials|secrets?)\b",
)


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    run_dir: Path
    state_path: Path
    fields: dict[str, str]
    directive_bound: bool
    judge_active: bool
    # Pass 12 (audit Cluster K): bridge model for intake runs. When the
    # intake skill drafts a run but the pipeline hasn't started, the
    # control-state writes `active_run: drafting` (not `true`). Hook
    # callers can downgrade enforcement from deny to warn for drafting
    # runs — operators still see the scope/manifest context, but the
    # gates don't block on artifacts the operator is mid-draft on.
    is_drafting: bool = False


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def repo_root_from_event(event: dict[str, Any]) -> Path:
    """Resolve the project root from a hook event.

    Cowork's Code tab roots cwd at .klodock rather than the picked
    project folder, so prefer CLAUDE_PROJECT_DIR when set. Fall back
    to event.cwd, then to the nearest ancestor with .agent-runs/
    or .claude-plugin/ or .git/.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir).resolve()
    cwd = event.get("cwd") or os.getcwd()
    path = Path(str(cwd)).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".agent-runs").exists() or (candidate / ".claude-plugin").exists() or (candidate / ".git").exists():
            return candidate
    return path


def parse_control_state(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def discover_active_runs(repo_root: Path) -> list[ActiveRun]:
    """Return active runs found under ``.agent-runs/``.

    Pass 12 (audit Cluster K) extends this to also pick up intake-staged
    runs that carry ``active_run: drafting`` in their
    ``active-control-state.md``. Drafting runs are returned with
    ``is_drafting=True`` so hook callers can downgrade enforcement —
    warn-not-block — until the operator promotes the run via
    ``/agent-pipeline-claude:run resume <id>``.
    """
    base = repo_root / ".agent-runs"
    if not base.exists():
        return []
    runs: list[ActiveRun] = []
    for state_path in sorted(base.glob("*/active-control-state.md")):
        fields = parse_control_state(state_path.read_text(encoding="utf-8-sig", errors="replace"))
        state_value = fields.get("active_run", "").lower()
        if state_value not in {"true", "drafting"}:
            continue
        is_drafting = (state_value == "drafting")
        run_dir = state_path.parent
        runs.append(
            ActiveRun(
                run_id=run_dir.name,
                run_dir=run_dir,
                state_path=state_path,
                fields=fields,
                directive_bound=_directive_bound(run_dir),
                judge_active=(repo_root / ".pipelines" / "action-classification.yaml").exists(),
                is_drafting=is_drafting,
            )
        )
    return runs


def latest_run(repo_root: Path) -> Path | None:
    base = repo_root / ".agent-runs"
    if not base.exists():
        return None
    candidates = [path for path in base.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def session_context(runs: list[ActiveRun]) -> str:
    if not runs:
        return ""
    lines = ["Agent Pipeline active run context:"]
    for run in runs:
        state_label = "DRAFTING (intake-staged)" if run.is_drafting else "ACTIVE"
        lines.append(
            "- "
            f"run={run.run_id} [{state_label}]; "
            f"stage={run.fields.get('current_stage', '(unknown)')}; "
            f"next={run.fields.get('next_required_action', '(unspecified)')}; "
            f"continuing_to={run.fields.get('continuing_to', '(unspecified)')}; "
            f"stop_condition={run.fields.get('stop_condition', '(unset)')}; "
            f"directive_bound={str(run.directive_bound).lower()}; "
            f"judge_active={str(run.judge_active).lower()}."
        )
        handoff = read_memory_handoff(run)
        if handoff:
            lines.append("")
            lines.append(handoff)
    if any(run.is_drafting for run in runs):
        lines.append(
            "DRAFTING runs: intake skill staged the manifest/scope-lock but the "
            "pipeline hasn't started. Scope guards are ADVISORY in this state — "
            "you'll see warnings but won't be auto-denied for scope violations. "
            "Resume the run with `/agent-pipeline-claude:run resume <run-id>` "
            "after the intake TODOs are filled in."
        )
    lines.append("Respect run.log, manifest.yaml, scope-lock.yaml, directive.yaml, and active-control-state.md before stopping or changing scope.")
    return "\n".join(lines)


def read_memory_handoff(run: ActiveRun) -> str:
    path = run.run_dir / "memory" / "handoff_current.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not text:
        return ""
    return "Agent Pipeline persistent memory:\n" + _truncate(text, 2400)


# --- v2.2.0: memory-rule scope override ----------------------------------
#
# Closes the proximate cause of the v2.0.x "modal pumping" failure mode:
# operator-layer memory rules (notably feedback_no_unilateral_product_
# decisions.md) get loaded into every Claude Code session BEFORE the LLM's
# first turn. When those rules conflict with the pipeline's v1.3.0
# modal-eliminating design, the LLM reads both and lets the older / more
# conservative one win, manufacturing modals between gates.
#
# Fix: SessionStart hook emits an additionalContext override block when
# (a) an active non-drafting pipeline run exists AND
# (b) pipelines/memory-scope-allowlist.yaml lists memory files that
#     resolve in the user's memory directory.
# The block tells the LLM that the listed rules are suspended for the
# duration of the run. It does not modify the memory files themselves
# (the harness loads them earlier and we can't unload), but the override
# takes precedence in the LLM's reading order.
#
# Backstop: the v2.2.0 modal-budget hook will mechanically deny any
# AskUserQuestion fired outside the declared gates, even if the LLM
# ignores this framing context.

_MEMORY_OVERRIDE_ALLOWLIST_FILENAME = "memory-scope-allowlist.yaml"


def _memory_override_allowlist_path(repo_root: Path) -> Path | None:
    """Locate the allowlist YAML.

    Resolution order:
      1. ``<repo_root>/.pipelines/memory-scope-allowlist.yaml`` -- the
         project's scaffolded copy (per the consumer-project layout).
      2. ``<plugin_root>/pipelines/memory-scope-allowlist.yaml`` -- the
         plugin source's canonical copy.
    Returns the first that exists, or None.
    """
    candidates = [
        repo_root / ".pipelines" / _MEMORY_OVERRIDE_ALLOWLIST_FILENAME,
        Path(__file__).resolve().parents[1] / "pipelines" / _MEMORY_OVERRIDE_ALLOWLIST_FILENAME,
    ]
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except OSError:
            continue
    return None


def _parse_memory_override_allowlist(yaml_path: Path) -> list[dict[str, str]]:
    """Tiny line-based parser for the memory-scope-allowlist schema.

    Expected shape:

        memory_overrides:
          - file: <basename>
            reason: "<single-line quoted string>"
          - file: ...
            reason: ...

    Returns a list of {"file": str, "reason": str} dicts. Tolerates
    comments, blank lines, and minor whitespace variation. Designed to
    parse the restricted schema without pulling in pyyaml -- the same
    pattern used elsewhere in this module
    (``_gate_stages_from_yaml``).
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    try:
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    in_memory_overrides = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Top-level key
        if stripped.startswith("memory_overrides:"):
            in_memory_overrides = True
            continue
        if not in_memory_overrides:
            continue
        # New entry: `- file: <name>` on a single line, or `- ` then
        # `file:` on the next line. We support both shapes.
        if stripped.startswith("- "):
            if current is not None:
                if current.get("file"):
                    entries.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest.startswith("file:"):
                current["file"] = rest.split(":", 1)[1].strip().strip('"').strip("'")
            continue
        if current is None:
            continue
        if stripped.startswith("file:"):
            current["file"] = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            continue
        if stripped.startswith("reason:"):
            rest = stripped[len("reason:"):].strip()
            # Strip a single layer of surrounding quotes; tolerate
            # bare strings too.
            if (rest.startswith('"') and rest.endswith('"')) or (
                rest.startswith("'") and rest.endswith("'")
            ):
                rest = rest[1:-1]
            current["reason"] = rest
            continue
    if current is not None and current.get("file"):
        entries.append(current)
    return entries


def _user_memory_search_roots() -> list[Path]:
    """Return candidate user-memory directories to search.

    1. ``$CLAUDE_USER_MEMORY_DIR`` if set (operator override + test
       hook).
    2. Every ``~/.claude/projects/<encoded>/memory/`` directory that
       exists (Claude Code's convention for per-workspace memory).
    """
    roots: list[Path] = []
    env_hint = os.environ.get("CLAUDE_USER_MEMORY_DIR")
    if env_hint:
        try:
            roots.append(Path(env_hint).expanduser().resolve())
        except (OSError, ValueError):
            pass
    try:
        base = Path.home() / ".claude" / "projects"
        if base.exists():
            for proj in sorted(base.iterdir()):
                mem = proj / "memory"
                try:
                    if mem.is_dir():
                        roots.append(mem.resolve())
                except OSError:
                    continue
    except (OSError, RuntimeError):
        pass
    return roots


def _resolve_user_memory_file(filename: str) -> Path | None:
    """Find a user memory file by basename. Returns first existing match."""
    if not filename:
        return None
    for root in _user_memory_search_roots():
        candidate = root / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def memory_override_context(repo_root: Path, runs: list[ActiveRun]) -> str:
    """v2.2.0 SessionStart memory-rule override block.

    Returns an ``additionalContext`` string telling the LLM which
    user-memory rules are suspended for the active run, or ``""`` if
    no override applies (no active run, all drafting, allowlist
    missing/empty, or no listed files resolve in the user's memory
    dir).

    The block is concatenated with ``session_context`` in
    ``handle_session_start``. It does not replace the memory files;
    Claude Code loads those earlier in the context window. The
    override takes precedence by virtue of appearing later (LLM
    instruction-following convention) and by virtue of the v2.2.0
    modal-budget hook denying any modal that violates the suspension.
    """
    non_drafting = [r for r in runs if not r.is_drafting]
    if not non_drafting:
        return ""
    allowlist_path = _memory_override_allowlist_path(repo_root)
    if allowlist_path is None:
        return ""
    entries = _parse_memory_override_allowlist(allowlist_path)
    if not entries:
        return ""
    resolved: list[tuple[str, Path, str]] = []
    for entry in entries:
        filename = entry.get("file") or ""
        if not filename:
            continue
        path = _resolve_user_memory_file(filename)
        if path is None:
            continue
        resolved.append((filename, path, entry.get("reason", "")))
    if not resolved:
        return ""
    run = non_drafting[0]
    lines = [
        "=== MEMORY OVERRIDES FOR THIS PIPELINE RUN ===",
        f"Active run: {run.run_id}",
        "",
        "The following user-memory rules are SUSPENDED for the duration of",
        "this /agent-pipeline-claude:run. They appear earlier in your",
        "context window because Claude Code loads memory files into every",
        "session. The override below takes precedence for this run only.",
        "",
    ]
    for filename, path, reason in resolved:
        lines.append(f"- {filename}")
        lines.append(f"  ({path})")
        if reason:
            lines.append(f"  reason: {reason}")
        lines.append("")
    lines.append(
        "After this run ends (active-control-state.md shows active_run: "
        "false or the run dir is absent), the rules reactivate."
    )
    lines.append(
        "Backstop: the modal-budget hook (v2.1.0) and policy-recheck hook "
        "(v2.2.0) mechanically enforce the design these overrides codify."
    )
    return "\n".join(lines)


# --- v2.2.1: stale plugin cache hygiene -----------------------------------
#
# Cowork's plugin manager updates `installed_plugins.json` to point at the
# new version's `installPath` on upgrade, but does NOT delete the prior
# version's cache directory. After 2-3 upgrades the cache parent
# accumulates several MB of dead code, all of which is:
#   - confusing during debugging ("which version is loaded?")
#   - vulnerable to accidental repoint (manually editing
#     installed_plugins.json to a stale version that still exists on disk)
#   - wasted disk space (1.5-2 MB per version)
#
# Per Scott 2026-05-20 directive ("I don't want shit hanging around after
# an upgrade"), the plugin self-cleans on first SessionStart after
# install: any sibling version directory whose semver is strictly less
# than the currently-loaded version gets removed.
#
# Safeguards:
#   - Only directories whose name parses as `MAJOR.MINOR.PATCH` semver
#     are considered (won't touch __pycache__, *.bak, etc.)
#   - Only versions strictly LESS than current are deleted (no rollback
#     friction during a deliberate downgrade ... well, slight friction:
#     a downgrade requires re-install from marketplace, but the install
#     mechanism handles that)
#   - Never deletes the current plugin's own directory
#   - Sanity check: cache_parent must contain the current dir as a direct
#     child (otherwise we're not in a Cowork cache layout)
#   - Per-dir delete failures are swallowed (best-effort; if a file is
#     locked, the next SessionStart will try again)
#   - Idempotent: returns [] when nothing to clean

_SEMVER_DIR_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _parse_semver(name: str) -> tuple[int, int, int] | None:
    """Parse a directory name as `MAJOR.MINOR.PATCH`. Return tuple or None."""
    if not _SEMVER_DIR_PATTERN.match(name):
        return None
    try:
        major_s, minor_s, patch_s = name.split(".")
        return (int(major_s), int(minor_s), int(patch_s))
    except (ValueError, AttributeError):
        return None


def cleanup_stale_plugin_caches(
    plugin_root: Path | None = None,
) -> list[str]:
    """v2.2.1: delete cache directories of prior plugin versions.

    Called from `handle_session_start` once per session (idempotent).
    See module-level comment block above for design rationale.

    Args:
        plugin_root: optional override for testing. Defaults to the
                     directory one level above this file's parent (since
                     hook_utils.py lives in `<plugin_root>/hooks/`).

    Returns:
        List of deleted directory names (e.g. `["2.0.0", "2.1.0"]`),
        sorted by version ascending. Empty list when nothing to clean.
    """
    import shutil
    if plugin_root is None:
        try:
            plugin_root = Path(__file__).resolve().parents[1]
        except (OSError, IndexError):
            return []
    current_name = plugin_root.name
    current_semver = _parse_semver(current_name)
    if current_semver is None:
        # Not running from a versioned cache dir (likely a dev checkout
        # like `agent-pipeline-claude-review/` or a test fixture).
        # Refuse to touch anything.
        return []
    cache_parent = plugin_root.parent
    # Sanity: the current dir must exist under cache_parent (i.e. we're
    # in a real Cowork cache layout, not some symlinked test setup).
    try:
        if not (cache_parent / current_name).is_dir():
            return []
    except OSError:
        return []
    deleted: list[str] = []
    try:
        siblings = sorted(cache_parent.iterdir())
    except OSError:
        return []
    for sibling in siblings:
        if sibling.name == current_name:
            continue
        try:
            if not sibling.is_dir():
                continue
        except OSError:
            continue
        sibling_semver = _parse_semver(sibling.name)
        if sibling_semver is None:
            # Not a versioned dir (e.g. __pycache__, tmp files). Skip.
            continue
        if sibling_semver >= current_semver:
            # Same or higher version: shouldn't normally happen, but if
            # it does (e.g. an aborted upgrade), don't delete it.
            continue
        try:
            shutil.rmtree(sibling, ignore_errors=False)
            deleted.append(sibling.name)
        except OSError:
            # Best-effort: a locked file or permission error leaves the
            # dir for the next SessionStart to retry. Don't crash the
            # hook over hygiene.
            continue
    return deleted


# --- v2.2.2: loud marketplace-update-available warning -------------------
#
# Per the Claude Code docs (https://code.claude.com/docs/en/discover-plugins
# #configure-auto-updates):
#
#   "Claude Code can automatically update marketplaces and their installed
#    plugins at startup. Official Anthropic marketplaces have auto-update
#    enabled by default. THIRD-PARTY AND LOCAL DEVELOPMENT MARKETPLACES
#    HAVE AUTO-UPDATE DISABLED BY DEFAULT."
#
# agent-pipeline-claude is a third-party marketplace -- so after the
# operator does `git pull && git checkout vX.Y.Z` on the marketplace
# clone, Cowork will NOT install the new version on its own. The plugin
# stays at the previously-installed version forever until the operator
# explicitly runs `claude plugin install` or enables auto-update via
# the `/plugin` UI.
#
# v2.2.1 shipped the cache-hygiene feature without flagging this gotcha
# -- and the gotcha defeated the feature in practice (the v2.2.1 hooks
# never loaded on the first user who tried to upgrade). v2.2.2 closes
# the awareness gap by detecting the version-skew at SessionStart and
# emitting a LOUD additionalContext block with the exact upgrade command.

_INSTALLED_PLUGINS_JSON_NAME = "installed_plugins.json"


def _read_installed_plugin_sha(
    claude_plugins_root: Path, plugin_name: str, marketplace_name: str
) -> str | None:
    """Look up the installed gitCommitSha for ``<plugin>@<marketplace>``.

    Reads ``<claude_plugins_root>/installed_plugins.json``. Returns the
    SHA string, or None if the file is missing, unparseable, or doesn't
    contain an entry for this plugin.
    """
    ip = claude_plugins_root / _INSTALLED_PLUGINS_JSON_NAME
    try:
        if not ip.is_file():
            return None
    except OSError:
        return None
    try:
        data = json.loads(ip.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    key = plugin_name + "@" + marketplace_name
    entries = plugins.get(key)
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    sha = first.get("gitCommitSha")
    return sha if isinstance(sha, str) and sha else None


def _read_marketplace_head_sha(marketplace_clone: Path) -> str | None:
    """Run ``git rev-parse HEAD`` in the marketplace clone, return SHA or None.

    Tolerates: missing dir, missing .git/, git not on PATH, non-zero
    exit, timeout. All return None silently -- the warning should fail
    quietly when the layout doesn't fit the expected Cowork shape.
    """
    import subprocess
    try:
        if not marketplace_clone.is_dir():
            return None
        if not (marketplace_clone / ".git").exists():
            return None
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(marketplace_clone),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha if sha else None


def marketplace_update_available_context(
    plugin_root: Path | None = None,
) -> str | None:
    """v2.2.2: detect installed-vs-marketplace SHA skew and emit a LOUD warning.

    Resolves the plugin's marketplace clone via the standard Cowork
    layout (see module-level comment block above), runs ``git rev-parse
    HEAD`` against the clone, compares against the gitCommitSha recorded
    in ``installed_plugins.json``. If they differ, returns a multi-line
    additionalContext block with the exact ``claude plugin install``
    command + auto-update toggle instructions.

    Returns None (silent) when:
      - layout resolution fails (dev checkout, fixture, unusual cache path)
      - marketplace clone is missing or not a git repo
      - git is unavailable / rev-parse exits non-zero
      - installed_plugins.json is missing or unparseable
      - the two SHAs match (we're up to date -- nothing to warn about)

    Status read only -- never modifies anything.
    """
    if plugin_root is None:
        try:
            plugin_root = Path(__file__).resolve().parents[1]
        except (OSError, IndexError):
            return None
    # Layout:
    #   plugin_root        = <claude_plugins>/cache/<marketplace>/<plugin>/<version>/
    #   plugin_root.parent = <claude_plugins>/cache/<marketplace>/<plugin>/
    #   parents[1]         = <claude_plugins>/cache/<marketplace>/
    #   parents[2]         = <claude_plugins>/cache/
    #   parents[3]         = <claude_plugins>/
    try:
        plugin_name = plugin_root.parent.name
        marketplace_name = plugin_root.parents[1].name
        cache_root = plugin_root.parents[2]
        claude_plugins_root = plugin_root.parents[3]
    except (IndexError, AttributeError):
        return None
    if cache_root.name != "cache":
        return None
    if claude_plugins_root.name != "plugins":
        return None
    marketplace_clone = claude_plugins_root / "marketplaces" / marketplace_name
    head_sha = _read_marketplace_head_sha(marketplace_clone)
    if head_sha is None:
        return None
    installed_sha = _read_installed_plugin_sha(
        claude_plugins_root, plugin_name, marketplace_name
    )
    if installed_sha is None:
        return None
    if head_sha == installed_sha:
        return None
    short_head = head_sha[:7]
    short_installed = installed_sha[:7]
    plugin_at_marketplace = plugin_name + "@" + marketplace_name
    lines = [
        "=== " + plugin_name.upper() + " UPDATE AVAILABLE ===",
        "",
        "Marketplace clone HEAD:    " + short_head + " (" + head_sha + ")",
        "Currently installed:       " + short_installed + " (" + installed_sha + ")",
        "",
        "**Auto-update is OFF by default for third-party marketplaces.**",
        "(Per https://code.claude.com/docs/en/discover-plugins#configure-auto-updates)",
        "",
        "Restarting Claude Code / Cowork does NOT install marketplace",
        "updates on its own for third-party marketplaces. The " + plugin_name,
        "plugin will keep loading the old version (" + short_installed + ")",
        "until the operator does one of:",
        "",
        "**Option 1 -- install the new version explicitly (recommended one-time upgrade):**",
        "",
        "    claude plugin install " + plugin_at_marketplace,
        "",
        "**Option 2 -- enable auto-update so future releases install automatically:**",
        "",
        "    Run `/plugin` in Cowork -> Marketplaces tab -> select",
        "    " + marketplace_name + " -> Enable auto-update.",
        "    Then restart Cowork (auto-update runs at startup).",
        "",
        "After install, run `/reload-plugins` or restart Cowork for the new",
        "hooks to load in your current session.",
        "",
        "**IMPORTANT FOR THE LLM READING THIS CONTEXT:** surface this",
        "warning to the operator at the top of your first response. Quote",
        "the exact `claude plugin install` command. Do not bury it. The",
        "operator may not have realized that third-party marketplaces don't",
        "auto-install updates -- the previous release (v2.2.1) shipped a",
        "feature that depended on this and we hit the gotcha in production.",
    ]
    return "\n".join(lines)


def stale_skill_context(prompt: str) -> str:
    lowered = prompt.lower()
    hits = []
    for skill in sorted(STALE_STANDALONE_SKILLS):
        if f"{NAMESPACED_PREFIX}{skill}" in lowered:
            continue
        if re.search(rf"(?<![\w:-]){re.escape(skill)}(?![\w:-])", lowered):
            hits.append(skill)
    if not hits:
        return ""
    replacements = ", ".join(f"{NAMESPACED_PREFIX}{name}" for name in hits)
    return f"Use namespaced Agent Pipeline skills to avoid stale standalone skills: {replacements}."


def prompt_bypass_context(prompt: str, runs: list[ActiveRun]) -> tuple[bool, str]:
    if not runs:
        return False, ""
    lowered = prompt.lower()
    bypass_terms = ("skip the gate", "bypass the gate", "ignore the manifest", "ignore scope-lock", "outside scope", "skip approval")
    if not any(term in lowered for term in bypass_terms):
        return False, ""
    return (
        True,
        "Active Agent Pipeline run detected. Do not bypass manifest, scope-lock, directive, judge, or human-gate protections; replan or ask for explicit operator authorization instead.",
    )


def tool_command(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
        return json.dumps(tool_input, sort_keys=True)
    if isinstance(tool_input, str):
        return tool_input
    return ""


# Tool names whose semantics are read-only on the local filesystem.
# These never trigger the "contract artifact touched" warning even if
# their tool_input mentions a contract filename (audit Pass 10 /
# Cluster I). Anything not in this set falls through to the existing
# write-class detection.
_READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset({
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "TodoWrite",  # writes to TodoList, not filesystem
})

# Bash subcommand tokens that are read-only on the local filesystem.
# A `bash` event whose first token (post-leading-newlines and after
# `cd … && `) matches one of these gets the same read-only treatment.
_READ_ONLY_BASH_TOKENS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more",
    "grep", "rg", "egrep", "fgrep",
    "ls", "dir", "tree",
    "wc", "find", "stat", "file",
    "git",  # git status / log / diff — write subcommands (commit, push) are caught by DESTRUCTIVE/EXTERNAL patterns
    "echo", "printf",
    # PowerShell readers
    "Get-Content", "Select-String", "Get-ChildItem", "Get-Item",
})


def _is_read_only_operation(event_or_command: Any) -> bool:
    """Return True for tool invocations that don't write to the local
    filesystem (audit Pass 10 / Cluster I).

    Used to suppress the "pipeline contract artifact touched" warning
    on Read-class tools and `cat manifest.yaml`-style Bash invocations.
    The pre-Pass-10 check warned on any tool_input containing
    `manifest.yaml`/`directive.yaml`/`scope-lock.yaml` regardless of
    intent — operators reading the file (which is the safe, encouraged
    behavior) saw the same noise as operators writing it.
    """
    if not isinstance(event_or_command, dict):
        return False
    tool_name = event_or_command.get("tool_name") or ""
    if isinstance(tool_name, str) and tool_name in _READ_ONLY_TOOL_NAMES:
        return True
    # Bash: inspect the first non-cd token.
    if tool_name == "Bash":
        cmd = tool_command(event_or_command).strip()
        if not cmd:
            return False
        # Strip leading `cd <dir> && ` chains (multi-step shell).
        while cmd.startswith("cd ") and "&&" in cmd:
            cmd = cmd.split("&&", 1)[1].strip()
        first_token = cmd.split(None, 1)[0] if cmd else ""
        # Heuristic guard: `git diff > out.txt` etc. is technically
        # write-class (redirect). Don't classify as read-only.
        if any(redir in cmd for redir in (">", ">>", "|tee", "| tee")):
            return False
        return first_token in _READ_ONLY_BASH_TOKENS
    return False


# --- v2.1.0: modal-budget enforcement -------------------------------------
#
# Background: v1.3.0 replaced chat-APPROVE gates with AskUserQuestion
# modal gates, on the premise that "one modal, one click, gate advances"
# eliminates the interpretive surface where the LLM either invented
# extra prompts or chickened out. That worked for the framework's OWN
# gates. It did NOT stop the orchestrator from manufacturing modal
# AskUserQuestion prompts BETWEEN gates — turning what should be three
# clicks (manifest, plan, manager) into 15+ clicks per run.
#
# v2.1.0 enforces a strict modal budget: during an active non-drafting
# pipeline run, AskUserQuestion is permitted ONLY when the orchestrator
# is at one of the pipeline yaml's declared `gate: human_approval`
# stages. Anything else is the "extra prompts" failure mode v1.3.0
# was supposed to eliminate; this hook closes the loophole.
#
# Drafting runs (intake mid-flight) bypass this: the intake skill
# itself asks one question if information is missing, which is legitimate
# pre-promotion modal use.

_GATE_STAGE_NAMES_CACHE: dict[Path, frozenset[str]] = {}


def _pipeline_yaml_for_run(run: ActiveRun) -> Path | None:
    """Resolve the pipeline yaml the run was started against.

    Convention: project root = run.run_dir.parent.parent (i.e. up from
    `.agent-runs/<id>/` to project root). Pipeline yaml lives at
    `.pipelines/<type>.yaml`. Type is read from the manifest if
    available, falling back to `feature` (the default pipeline).
    """
    try:
        project_root = run.run_dir.parent.parent
        manifest_path = run.run_dir / "manifest.yaml"
        pipeline_type = "feature"
        if manifest_path.exists():
            for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("type:"):
                    _, _, value = stripped.partition(":")
                    candidate = value.strip().strip("\"'")
                    if candidate:
                        pipeline_type = candidate
                    break
        yaml_path = project_root / ".pipelines" / f"{pipeline_type}.yaml"
        if yaml_path.exists():
            return yaml_path
    except Exception:
        # Hook code must never raise. If we can't find the yaml,
        # caller treats the budget as un-enforceable (allow).
        return None
    return None


def _gate_stages_from_yaml(yaml_path: Path) -> frozenset[str]:
    """Return the set of stage names declared as `gate: human_approval`."""
    cached = _GATE_STAGE_NAMES_CACHE.get(yaml_path)
    if cached is not None:
        return cached
    gate_stages: set[str] = set()
    try:
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
        current_name: str | None = None
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if stripped.startswith("- name:"):
                _, _, value = stripped.partition(":")
                current_name = value.strip().strip("\"'") or None
            elif stripped.startswith("gate:"):
                _, _, value = stripped.partition(":")
                if value.strip().strip("\"'") == "human_approval" and current_name:
                    gate_stages.add(current_name)
    except Exception:
        pass
    frozen = frozenset(gate_stages)
    _GATE_STAGE_NAMES_CACHE[yaml_path] = frozen
    return frozen


# --- v2.1.0: stage-artifact format conformance ----------------------------
#
# auto_promote.py is a mechanical pattern-matcher. It scans stage
# artifacts for specific marker lines and reports ELIGIBLE / NOT_ELIGIBLE
# based on whether those markers are present. The role files (verifier,
# critic, drift-detector) reference the markers in prose but don't
# enforce them; agents routinely produce freeform prose verdicts without
# the marker line, defeating auto-promote and routing the run through
# manual manager gate even when the quality work was clean.
#
# This hook intercepts Write tool calls targeting the three stage
# artifacts. If the content lacks its required marker line, deny the
# write with a message naming the missing pattern. Agent re-edits with
# the marker; the marker fires auto-promote correctly downstream.

import re as _re_for_artifact_format

_ARTIFACT_FORMAT_REQUIREMENTS: dict[str, tuple[str, str]] = {
    "verifier-report.md": (
        r"\*\*Criteria:\s*\d+\s+total,\s*\d+\s+MET,\s*\d+\s+PARTIAL,\s*\d+\s+NOT\s+MET,\s*\d+\s+NOT\s+APPLICABLE\*\*",
        "**Criteria: <T> total, <M> MET, <P> PARTIAL, <N> NOT MET, <A> NOT APPLICABLE**",
    ),
    "critic-report.md": (
        r"\*\*Findings:\s*\d+\s+total,\s*\d+\s+blocker,\s*\d+\s+critical,\s*\d+\s+major,\s*\d+\s+minor\*\*",
        "**Findings: <T> total, <B> blocker, <C> critical, <M> major, <N> minor**",
    ),
    "drift-report.md": (
        r"\*\*Drift:\s*\d+\s+total,\s*\d+\s+blocker\*\*",
        "**Drift: <T> total, <B> blocker**",
    ),
}


def stage_artifact_format_decision(
    event: dict[str, Any], runs: list[ActiveRun]
) -> dict[str, Any] | None:
    """Deny Write calls saving stage reports without the auto-promote marker.

    Triggers ONLY for the three stage-report filenames inside an active
    non-drafting run. Reads the inbound content from tool_input.content,
    scans for the required marker pattern, denies if absent.
    """
    tool_name = event.get("tool_name") or ""
    if tool_name != "Write":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path") or ""
    content = tool_input.get("content") or ""
    if not isinstance(file_path, str) or not isinstance(content, str):
        return None
    # Only fire under an active non-drafting run.
    non_drafting = [r for r in runs if not r.is_drafting]
    if not non_drafting:
        return None
    # Match the filename against our artifact list.
    name = Path(file_path).name
    if name not in _ARTIFACT_FORMAT_REQUIREMENTS:
        return None
    # Confirm this write is INSIDE an .agent-runs/<id>/ dir of an
    # active run (don't fire on test fixtures / docs that happen to
    # share the filename).
    in_run_dir = False
    try:
        resolved = Path(file_path).resolve()
        for r in non_drafting:
            try:
                resolved.relative_to(r.run_dir.resolve())
                in_run_dir = True
                break
            except ValueError:
                continue
    except Exception:
        return None
    if not in_run_dir:
        return None
    pattern_re, example = _ARTIFACT_FORMAT_REQUIREMENTS[name]
    if _re_for_artifact_format.search(pattern_re, content):
        return None  # marker present; allow
    reason = (
        "STAGE_ARTIFACT_FORMAT_VIOLATION: "
        + name
        + " is missing the required auto-promote marker line. "
        + "Expected a line matching the pattern: " + example + ". "
        + "Without this marker, scripts/policy/auto_promote.py cannot "
        + "parse the artifact and the run will route through the manual "
        + "manager gate instead of evidence-driven auto-promote. Add the "
        + "marker line with accurate counts (one per category) and retry "
        + "the write."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def modal_budget_decision(
    event: dict[str, Any], runs: list[ActiveRun]
) -> dict[str, Any] | None:
    """v2.2.1: Deny ALL ``AskUserQuestion`` during active non-drafting runs.

    v2.2.1 removed modal gates entirely (the operator UX failed -- Cowork's
    modal overlay hides the chat context needed at gate-decision time).
    Gates are now chat-based with deterministic first-token keyword
    parsing. Therefore: there are NO legitimate ``AskUserQuestion`` calls
    during an active non-drafting pipeline run. The modal-budget hook
    denies every one with ``MODAL_BUDGET_EXCEEDED`` and points the
    orchestrator at the chat-based gate / adopt-and-proceed pattern.

    Permits the modal only when:
      - no active non-drafting run exists (operator ad-hoc use is fine)
      - all active runs are drafting (intake bridge state -- the operator
        is mid-draft and can use modals for intake clarifications)

    Pre-v2.2.1 behavior: permitted modals at declared ``gate:
    human_approval`` stages (manifest, plan, manager). v2.2.1 removes the
    gate-stage exception because gates are no longer modal.
    """
    tool_name = event.get("tool_name") or ""
    if tool_name != "AskUserQuestion":
        return None
    non_drafting = [r for r in runs if not r.is_drafting]
    if not non_drafting:
        return None  # no active run, or all drafting
    reason = (
        "MODAL_BUDGET_EXCEEDED: v2.2.1 removed AskUserQuestion modals from "
        "pipeline gates because the Cowork modal overlay hides chat context "
        "the operator needs at gate-decision time. There are NO legitimate "
        "modal calls during an active non-drafting pipeline run. "
        "Re-evaluate: (a) if you are at a manifest/plan/manager gate, print "
        "the structured chat gate prompt from skills/run/references/run.md "
        "Step 6/8/9 and wait for the operator to reply with the first-token "
        "keyword (APPROVE / REVISE / REPLAN / BLOCK / VIEW); (b) if you have "
        "a researcher/planner/critic recommendation that is not a gate "
        "decision, ADOPT it, record in director-decisions.md, narrate one "
        "line in chat, proceed (per the Adopt-and-proceed section of run.md); "
        "(c) if the decision is genuinely outside scope, surface BLOCK/REPLAN "
        "at the next mandated gate via chat -- not via modal."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def classify_tool_risk(event: dict[str, Any], runs: list[ActiveRun]) -> tuple[str, list[str]]:
    command = tool_command(event)
    haystack = command.lower()
    reasons: list[str] = []
    severity = "allow"
    if _matches_any(command, DESTRUCTIVE_PATTERNS):
        severity = "deny"
        reasons.append("destructive or irreversible command pattern")
    if _matches_any(command, SECRET_PATTERNS):
        severity = "deny"
        reasons.append("credential or secret exposure pattern")
    if _matches_any(command, EXTERNAL_OR_RELEASE_PATTERNS):
        if severity != "deny":
            severity = "warn"
        reasons.append("external-facing release, network, or push operation")
    if _matches_any(command, DEPENDENCY_PATTERNS):
        if severity != "deny":
            severity = "warn"
        reasons.append("dependency installation changes project state")
    if runs and _touches_outside_allowed_paths(event, runs[0].run_dir):
        severity = "deny"
        reasons.append("write target appears outside manifest allowed_paths during an active run")
    # v2.1.0: path-aware contract-artifact detection. The v2.0.x version
    # did a substring search on the lowercased command/content, which
    # produced false positives when the AGENT's own source/test code
    # mentioned "manifest.yaml" / "scope-lock.yaml" / "directive.yaml"
    # by NAME (e.g. writing test fixtures, documenting hooks, building
    # the policy scripts themselves). This refinement checks the actual
    # write TARGET path -- the only place "contract artifact touched"
    # genuinely indicates a contract mutation.
    contract_touched, contract_path = _is_contract_artifact_write(event, runs)
    if contract_touched:
        # Post-pin manifest mutations are DENY (not warn): the preflight
        # SHA pin marks the manifest as immutable for the rest of the
        # run. Editing it after the pin breaks the integrity contract
        # and forces auto-promote into the manual gate path.
        if (
            contract_path
            and contract_path.name == "manifest.yaml"
            and runs
            and (contract_path.parent / "manifest.sha").exists()
        ):
            severity = "deny"
            reasons.append(
                "post-pin manifest mutation: the run's manifest.sha pin file exists, "
                "marking this manifest as immutable. Any further edit breaks the "
                "integrity contract. If the manifest is genuinely wrong, BLOCK the "
                "current run and intake a corrected one."
            )
        elif severity != "deny":
            severity = "warn"
            reasons.append("pipeline contract artifact touched")
        else:
            reasons.append("pipeline contract artifact touched")
    # v0.4 judge routing. During a judged run, upgrade an external-facing or
    # release-class action from warn to a JUDGE_REVIEW_REQUIRED deny so the
    # executor's stop-and-propose protocol is non-bypassable. Placed LAST so
    # every absolute deny floor above (destructive, secret, write-outside-
    # allowed-paths, post-pin manifest) has already won: this branch fires
    # only when severity is not already "deny", so a judge allow or the
    # approval sidecar can NEVER reopen the floor. The orchestrator exempts
    # its single judge-approved call by writing the exact command to
    # judge-approved-next.txt; the match is consumed one-shot.
    if (
        severity != "deny"
        and runs
        and runs[0].judge_active
        and not runs[0].is_drafting
        and _matches_any(command, EXTERNAL_OR_RELEASE_PATTERNS)
    ):
        if _command_matches_judge_approval(runs[0], command):
            _consume_judge_approval(runs[0])
        else:
            severity = "deny"
            reasons.append(
                "JUDGE_REVIEW_REQUIRED: external-facing or release-class action "
                "blocked during a judged run. STOP and write a "
                "pending-action.yaml proposal (executor.md stop-and-propose "
                "protocol) and return; the orchestrator spawns the judge and "
                "runs the action only on an allow verdict (writing "
                "judge-approved-next.txt first). Direct execution is blocked."
            )
    return severity, reasons


_CONTRACT_ARTIFACT_NAMES = frozenset(
    {"manifest.yaml", "scope-lock.yaml", "directive.yaml", "active-control-state.md"}
)


def _is_contract_artifact_write(
    event: dict[str, Any], runs: list[ActiveRun]
) -> tuple[bool, Path | None]:
    """Return (True, target_path) iff the tool call writes to a contract artifact.

    Distinguishes write-class tools (Write, Edit, MultiEdit, NotebookEdit)
    where the target file_path is structured and unambiguous, from Bash
    commands where any string can appear. For Bash, we only flag commands
    that perform a write redirect or in-place edit referencing a contract
    artifact file in a path that resolves under an active run dir.

    Returns (False, None) for read-only tool calls, for write-class tools
    whose target isn't a contract artifact name, and for the agent's own
    source code / test fixtures that happen to mention contract artifact
    NAMES inside Bash command content or Write content (the v2.0.x false-
    positive class).
    """
    if not isinstance(event, dict):
        return (False, None)
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return (False, None)
    # Read-only tools never touch contract artifacts in a write sense.
    if tool_name in _READ_ONLY_TOOL_NAMES:
        return (False, None)
    # Write-class tools: file_path is structured.
    if tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        file_path = tool_input.get("file_path") or ""
        if not isinstance(file_path, str) or not file_path:
            return (False, None)
        try:
            resolved = Path(file_path).resolve()
        except Exception:
            return (False, None)
        if resolved.name not in _CONTRACT_ARTIFACT_NAMES:
            return (False, None)
        # Active-run match: best signal, gives us run_dir + the resolved
        # path for the post-pin deny check.
        for r in runs:
            try:
                resolved.relative_to(r.run_dir.resolve())
                return (True, resolved)
            except ValueError:
                continue
        # No active run match. Still flag if the path string indicates a
        # run dir (.agent-runs/<id>/manifest.yaml etc.) -- the agent is
        # touching a contract artifact even if our active-run discovery
        # didn't pick it up. Don't flag .pipelines/manifest-template.yaml
        # or similar non-run paths -- those are legitimate edits to
        # scaffolding/templates, not to a specific run's contract.
        normalized = file_path.replace("\\", "/")
        if "/.agent-runs/" in normalized or normalized.startswith(".agent-runs/"):
            return (True, resolved)
        return (False, None)
    # Bash: flag only when the command does a redirect or in-place edit
    # AGAINST a contract artifact path that lives in an active run dir.
    if tool_name == "Bash":
        if _is_read_only_operation(event):
            return (False, None)
        command = tool_command(event)
        haystack = command.lower()
        bash_contract = False
        for name in _CONTRACT_ARTIFACT_NAMES:
            if name in haystack:
                # Only count if the command does a write (redirect or
                # sed -i / tee / cp into). Reads/greps of these files
                # are legitimate.
                if (
                    " > " in command
                    or " >> " in command
                    or " >>" in command
                    or " >" in command
                    or "sed -i" in command
                    or "tee " in command
                    or "cp " in command
                    or "mv " in command
                ):
                    bash_contract = True
                    break
        if bash_contract:
            # Best-effort: figure out which file inside a run dir was
            # the target (for the post-pin manifest check downstream).
            # Resolution is approximate from a Bash command; the deny
            # check downstream tolerates a None path (only the warn
            # message fires, not the post-pin deny).
            return (True, None)
        return (False, None)
    return (False, None)


# v2.2.0: hook-acknowledgement enforcement.
#
# When a contract artifact (manifest.yaml, scope-lock.yaml, directive.yaml)
# is mutated, the run is in an unknown policy state. The orchestrator
# must re-run the corresponding policy check before any further write or
# release operation. This is the structural fix for the v2.0.x
# "noted, continuing" failure mode where contract-artifact-touched
# warnings were acknowledged conversationally and immediately ignored
# without any forcing function to actually verify the post-edit policy
# state.
#
# Mechanism: a sidecar file ``.agent-runs/<run-id>/pending-policy-recheck.txt``
# lists outstanding recheck commands, one per line. PreToolUse denies
# Write/Edit and non-recheck Bash while the sidecar is non-empty;
# Read/Grep/Glob remain allowed up to _MAX_READ_ONLY_BEFORE_RECHECK
# calls (tracked in a sibling counter file). PostToolUse appends on
# contract-artifact write success and pops on recheck Bash success.
#
# Only manifest.yaml / scope-lock.yaml / directive.yaml trigger the
# obligation. active-control-state.md is in _CONTRACT_ARTIFACT_NAMES
# for the warn-level "contract artifact touched" signal but is mutated
# by the orchestrator routinely during normal stage transitions — it is
# not an immutable contract and does not require a recheck.

_REQUIRED_RECHECK_FOR_CONTRACT_NAME: dict[str, str] = {
    "manifest.yaml": (
        "python scripts/policy/check_manifest_immutable.py "
        "--check --run {run_id}"
    ),
    "scope-lock.yaml": (
        "python scripts/policy/check_scope_lock.py --run {run_id}"
    ),
    "directive.yaml": (
        "python scripts/policy/check_directive_conformance.py --run {run_id}"
    ),
}

# Script names that count as a policy recheck. A Bash command satisfies
# a pending recheck line iff it invokes one of these scripts AND the
# script's filename also appears in that pending line. ``run_all.py``
# is the umbrella runner and pops every pending line when it succeeds.
_RECHECK_SCRIPT_NAMES: frozenset[str] = frozenset({
    "check_manifest_immutable.py",
    "check_scope_lock.py",
    "check_directive_conformance.py",
    "run_all.py",
})

# Maximum number of Read/Grep/Glob calls allowed between the most recent
# contract-artifact write and the required recheck. Set low enough that
# the obligation can't be deferred indefinitely behind "just one more
# read" calls.
_MAX_READ_ONLY_BEFORE_RECHECK: int = 3

_PENDING_RECHECK_SIDECAR = "pending-policy-recheck.txt"
_PENDING_RECHECK_COUNTER = "pending-policy-recheck-readcount.txt"


def _pending_recheck_path(run: ActiveRun) -> Path:
    return run.run_dir / _PENDING_RECHECK_SIDECAR


def _read_pending_recheck(run: ActiveRun) -> list[str]:
    sidecar = _pending_recheck_path(run)
    if not sidecar.exists():
        return []
    try:
        text = sidecar.read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def _write_pending_recheck(run: ActiveRun, lines: list[str]) -> None:
    sidecar = _pending_recheck_path(run)
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        if lines:
            sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif sidecar.exists():
            sidecar.unlink()
    except OSError:
        # Best-effort: a failure to update the sidecar must not crash the
        # hook. The deny on the next non-recheck write is the backstop.
        pass


def _read_only_counter_path(run: ActiveRun) -> Path:
    return run.run_dir / _PENDING_RECHECK_COUNTER


def _read_read_only_counter(run: ActiveRun) -> int:
    p = _read_only_counter_path(run)
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _set_read_only_counter(run: ActiveRun, value: int) -> None:
    p = _read_only_counter_path(run)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(max(0, value)), encoding="utf-8")
    except OSError:
        pass


def _clear_read_only_counter(run: ActiveRun) -> None:
    p = _read_only_counter_path(run)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _bash_matches_recheck(command: str, pending_lines: list[str]) -> str | None:
    """Return the pending line that a Bash command satisfies, or None.

    Match logic:
      * The command must mention one of ``_RECHECK_SCRIPT_NAMES``.
      * That script name must also appear in at least one pending line.
      * ``run_all.py`` is the umbrella runner; it satisfies any pending
        line and the caller pops everything in one shot.
    """
    if not command or not pending_lines:
        return None
    invoked: str | None = None
    for name in _RECHECK_SCRIPT_NAMES:
        if name in command:
            invoked = name
            break
    if invoked is None:
        return None
    if invoked == "run_all.py":
        return pending_lines[0]
    for line in pending_lines:
        if invoked in line:
            return line
    return None


# v0.4 judge layer — one-shot orchestrator approval sidecar.
#
# During a judged run (`.pipelines/action-classification.yaml` exists,
# surfaced as ActiveRun.judge_active) classify_tool_risk denies the
# executor's direct external-facing and release-class actions with a
# JUDGE_REVIEW_REQUIRED reason, so the stop-and-propose protocol is
# non-bypassable. The orchestrator runs a judge-approved action by writing
# the EXACT command to `judge-approved-next.txt` immediately before running
# it; the hook exempts that single matching call and consumes the sidecar so
# the exemption is one-shot. The destructive/secret deny floor is evaluated
# first and is never exempted by this sidecar (see classify_tool_risk).
_JUDGE_APPROVED_SIDECAR = "judge-approved-next.txt"


def _judge_approved_path(run: ActiveRun) -> Path:
    return run.run_dir / _JUDGE_APPROVED_SIDECAR


def _read_judge_approved(run: ActiveRun) -> str | None:
    sidecar = _judge_approved_path(run)
    if not sidecar.exists():
        return None
    try:
        text = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _command_matches_judge_approval(run: ActiveRun, command: str) -> bool:
    """True iff the orchestrator pre-approved this EXACT command.

    Exact, whitespace-stripped match only — a fuzzy or substring match could
    exempt more than the judge allowed.
    """
    approved = _read_judge_approved(run)
    if approved is None or not command:
        return False
    return command.strip() == approved


def _consume_judge_approval(run: ActiveRun) -> None:
    """Delete the approval sidecar so the exemption is one-shot.

    Best-effort: a failure to delete must not crash the hook — the next
    identical command would simply be re-denied, which is the safe direction.
    """
    sidecar = _judge_approved_path(run)
    if sidecar.exists():
        try:
            sidecar.unlink()
        except OSError:
            pass


def policy_recheck_decision(
    event: dict[str, Any], runs: list[ActiveRun]
) -> dict[str, Any] | None:
    """PreToolUse hook: enforce the hook-acknowledgement contract.

    Fires when any non-drafting active run has a non-empty
    ``pending-policy-recheck.txt`` sidecar. Allowed despite the sidecar:

      * Bash that matches a pending recheck command.
      * Read/Grep/Glob (and other read-only tools), up to
        ``_MAX_READ_ONLY_BEFORE_RECHECK`` calls since the most recent
        sidecar append. Once the budget is exhausted, read-only tools
        are also denied.
      * ``AskUserQuestion`` is delegated to ``modal_budget_decision``;
        we do not second-guess modal semantics here.

    Everything else is denied with ``POLICY_RECHECK_REQUIRED`` and a
    structured reason naming the next required recheck command.

    Fail-open: no active run, all runs in drafting state, no pending
    entries on any active run. The deny only fires when the system is
    genuinely in unknown policy state and the operator has not yet
    cleared it.
    """
    tool_name = event.get("tool_name") or ""
    if tool_name == "AskUserQuestion":
        return None
    pending_run: ActiveRun | None = None
    pending_lines: list[str] = []
    for run in runs:
        if run.is_drafting:
            continue
        lines = _read_pending_recheck(run)
        if lines:
            pending_run = run
            pending_lines = lines
            break
    if pending_run is None:
        return None
    next_recheck = pending_lines[0]
    if tool_name == "Bash":
        command = tool_command(event)
        if _bash_matches_recheck(command, pending_lines):
            return None
        reason = (
            "POLICY_RECHECK_REQUIRED: a pipeline contract artifact was "
            "modified and the policy state must be re-verified before "
            "any further write or release operation. Run this next: "
            "`" + next_recheck + "` "
            "(" + str(len(pending_lines)) + " pending in "
            + _PENDING_RECHECK_SIDECAR + "). Read/Grep/Glob remain "
            "allowed up to " + str(_MAX_READ_ONLY_BEFORE_RECHECK) + " "
            "calls before the recheck becomes mandatory. v2.2.0 hook "
            "enforces v1.3.0/v2.1.0 design that contract-artifact "
            "warnings are load-bearing obligations, not advisory."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    if tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        reason = (
            "POLICY_RECHECK_REQUIRED: a pipeline contract artifact was "
            "modified and the policy state must be re-verified before "
            "any further write. Run this next: `" + next_recheck + "`. "
            "Once the recheck exits 0 the pending line is popped and "
            "this write will be permitted."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    if tool_name in _READ_ONLY_TOOL_NAMES:
        count = _read_read_only_counter(pending_run)
        if count >= _MAX_READ_ONLY_BEFORE_RECHECK:
            reason = (
                "POLICY_RECHECK_REQUIRED: a pipeline contract artifact "
                "was modified and the "
                + str(_MAX_READ_ONLY_BEFORE_RECHECK)
                + "-call read-only budget has been exhausted ("
                + str(count) + " read-only ops since the sidecar was "
                "last appended-to). Run the recheck before any further "
                "read: `" + next_recheck + "`."
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        _set_read_only_counter(pending_run, count + 1)
        return None
    return None


def record_pending_recheck_for_write(
    event: dict[str, Any], runs: list[ActiveRun]
) -> str | None:
    """PostToolUse hook for successful Write/Edit/MultiEdit/NotebookEdit/Bash.

    If the tool call touched a contract artifact (manifest.yaml /
    scope-lock.yaml / directive.yaml) inside an active non-drafting
    run dir, append the required recheck command to that run's
    ``pending-policy-recheck.txt`` sidecar.

    Returns the appended (or already-present) line, or None if no
    contract write was detected. Idempotent: does not duplicate an
    existing pending line for the same artifact within the same run.
    Resets the read-only counter on every append so each fresh
    pending entry gets a fresh budget.
    """
    if not isinstance(event, dict):
        return None
    tool_name = event.get("tool_name") or ""
    if tool_name not in {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}:
        return None
    contract_touched, contract_path = _is_contract_artifact_write(event, runs)
    if not contract_touched or contract_path is None:
        # Bash write-redirects to contract artifacts return (True, None)
        # because Bash command resolution is approximate. In that
        # specific case the orchestrator should still re-verify, but
        # we can't reliably pick the right run+contract pairing without
        # the resolved path. Skipping is the safer default.
        return None
    name = contract_path.name
    cmd_template = _REQUIRED_RECHECK_FOR_CONTRACT_NAME.get(name)
    if cmd_template is None:
        return None
    for run in runs:
        if run.is_drafting:
            continue
        try:
            contract_path.relative_to(run.run_dir.resolve())
        except (ValueError, OSError):
            continue
        required = cmd_template.format(run_id=run.run_id)
        current = _read_pending_recheck(run)
        if required not in current:
            current.append(required)
            _write_pending_recheck(run, current)
        _clear_read_only_counter(run)
        return required
    return None


def pop_pending_recheck_on_bash_success(
    event: dict[str, Any], runs: list[ActiveRun]
) -> str | None:
    """PostToolUse hook for Bash that succeeded.

    If the command matches a pending recheck for any non-drafting
    active run, pop that line. ``run_all.py`` pops every pending line
    in one shot because it runs the full policy suite.

    Returns the popped line (or representative line for run_all), or
    None if no match.
    """
    if not isinstance(event, dict):
        return None
    if (event.get("tool_name") or "") != "Bash":
        return None
    response = event.get("tool_response") or {}
    if isinstance(response, dict):
        exit_code = response.get("exit_code")
        if exit_code not in (0, None, "0"):
            return None
        if response.get("success") is False:
            return None
    command = tool_command(event)
    if not command:
        return None
    for run in runs:
        if run.is_drafting:
            continue
        pending = _read_pending_recheck(run)
        if not pending:
            continue
        matched = _bash_matches_recheck(command, pending)
        if matched is None:
            continue
        if "run_all.py" in command:
            _write_pending_recheck(run, [])
        else:
            pending.remove(matched)
            _write_pending_recheck(run, pending)
        if not _read_pending_recheck(run):
            _clear_read_only_counter(run)
        return matched
    return None


def permission_decision(event: dict[str, Any], runs: list[ActiveRun]) -> dict[str, Any] | None:
    severity, reasons = classify_tool_risk(event, runs)
    # Pass 12 / Cluster K: when every active run is in drafting state
    # (intake-staged, pipeline not yet started), the scope guards are
    # advisory — we still surface reasons to the operator via the
    # session context, but we do NOT auto-deny on scope violations
    # alone. Destructive / secret-exposure patterns still deny because
    # those reasons are absolute, not run-scoped.
    only_drafting = bool(runs) and all(run.is_drafting for run in runs)
    if severity == "deny" and only_drafting:
        # Drop the run-scoped deny reasons; keep the absolute ones.
        absolute = [r for r in reasons if not _is_run_scoped_reason(r)]
        if not absolute:
            return None  # all reasons were scope-bound → no deny while drafting
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": (
                        "Agent Pipeline hook denied approval request "
                        "(drafting run; scope guards advisory, but absolute "
                        "policies still apply): " + "; ".join(absolute)
                    ),
                },
            }
        }
    if severity == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": "Agent Pipeline hook denied approval request: " + "; ".join(reasons),
                },
            }
        }
    if severity == "allow" and runs and runs[0].directive_bound and not runs[0].is_drafting:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    return None


_RUN_SCOPED_REASON_SUBSTRINGS: tuple[str, ...] = (
    "write target appears outside manifest allowed_paths",
    "pipeline contract artifact touched",
)


def _is_run_scoped_reason(reason: str) -> bool:
    """Return True for classify_tool_risk reasons that are bound to the
    active run's manifest/scope (Pass 12). Drafting runs downgrade these
    from deny to advisory because the manifest/scope itself is mid-
    draft. Absolute reasons (destructive command, credential exposure)
    are NOT in this list — they apply regardless of run state."""
    lowered = reason.lower()
    return any(needle in lowered for needle in _RUN_SCOPED_REASON_SUBSTRINGS)


def tool_failure_context(event: dict[str, Any]) -> str:
    response = event.get("tool_response")
    pieces: list[str] = []
    failed = _tool_response_failed(response)
    if failed:
        pieces.append("The last tool result appears to contain a failure. Inspect the command output, fix the root cause, and rerun the relevant verification before advancing the pipeline.")
    # v2.2.0: path-aware contract-artifact detection, matching the
    # v2.1.0 fix in classify_tool_risk. The pre-v2.2.0 version did a
    # substring search on the lowercased command/content, which
    # false-positived on every framework edit / test fixture / doc that
    # mentioned `manifest.yaml` / `scope-lock.yaml` / `directive.yaml`
    # by NAME -- e.g. tool_command() for a Write/Edit returns the
    # JSON-dumped tool_input, whose new_string can legitimately
    # contain those tokens as string literals. Using the path-aware
    # detector here closes the last remaining substring-only check
    # in production code (test_contract_artifact_precision.py pins
    # the contract for classify_tool_risk; test_hook_ack_enforcement
    # pins it for tool_failure_context).
    contract_touched, _path = _is_contract_artifact_write(event, [])
    if contract_touched:
        pieces.append("A pipeline contract artifact was touched. Re-run directive/scope/manifest policy checks before relying on any auto-approval.")
    command = tool_command(event).lower()
    if "pytest" in command and failed:
        pieces.append("Tests failed. Do not mark the stage complete until pytest is green or the failing gate records a valid human stop condition.")
    return "\n".join(pieces)


def _tool_response_failed(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    for name in ("exit_code", "exitCode", "returncode", "return_code", "status"):
        if name not in response:
            continue
        value = response.get(name)
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized.isdigit():
                return int(normalized) != 0
            if normalized in {"failed", "failure", "error"}:
                return True
            if normalized in {"ok", "success", "passed", "pass"}:
                return False
    stderr = str(response.get("stderr") or "")
    return bool(stderr.strip() and any(marker in stderr.lower() for marker in ("traceback", "error:", "exception")))


def stop_continuation(repo_root: Path) -> str:
    plugin_root = Path(__file__).resolve().parents[1]
    for import_root in (repo_root, plugin_root):
        root_text = str(import_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    from scripts.final_response_gate import evaluate_final_response_gate

    results = evaluate_final_response_gate(repo_root / ".agent-runs", require_active_run=False)
    blocked = [result for result in results if not result.allowed]
    if not blocked:
        return ""
    lines = ["Agent Pipeline run is not at a valid stop condition. Continue the run before sending a final response."]
    for result in blocked:
        lines.append(f"- {result.reason}")
        if result.continuing_to:
            lines.append(f"  continuing_to: {result.continuing_to}")
        if result.next_required_action:
            lines.append(f"  next_required_action: {result.next_required_action}")
    return "\n".join(lines)


def append_hook_event(repo_root: Path, event_name: str, message: str) -> None:
    runs = discover_active_runs(repo_root)
    if not runs:
        return
    path = runs[0].run_dir / "hook-events.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event_name,
        "message": message,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# Map hook event names -> default PRD FR-7 taxonomy type. Without this,
# every Layer A record was written with `metadata = {}` and the Layer
# A→B flush filter (which requires metadata.type in allowed_types)
# silently dropped 100% of them as `skipped_no_type` (audit Pass 9 /
# QA-001). Callers can still override by passing an explicit
# `metadata={"type": "..."}`.
#
# Categories chosen for default-most-useful retrieval semantics:
#   - PostToolUseFailure → `anti_pattern` so it surfaces under
#     "what failed last time."
#   - UserPromptSubmit → `session_state` to preserve dialog continuity
#     across compactions (PostCompact re-injects handoff_current.md).
#   - All other lifecycle events → `session_state`.
# Explicit metadata.type from the caller (open-loops, decisions) wins.
_EVENT_DEFAULT_TYPE: dict[str, str] = {
    "SessionStart": "session_state",
    "UserPromptSubmit": "session_state",
    "PreToolUse": "session_state",
    "PermissionRequest": "session_state",
    "PostToolUse": "session_state",
    "PostToolUseFailure": "anti_pattern",
    "PreCompact": "session_state",
    "PostCompact": "session_state",
    "SubagentStop": "session_state",
    "Stop": "session_state",
    "SessionEnd": "session_state",
}


def _redact_message_for_layer_a(message: str) -> tuple[str, bool, list[str]]:
    """Pre-write redaction (audit Pass 9 / ENG-008). Returns
    (sanitized_message, was_redacted, matched_patterns).

    Layer A writes happen unconditionally — they're the durable floor
    that survives Layer B (Mem0) outages. Before the fix, Bash commands
    with embedded secrets (e.g. ``curl -H "Authorization: Bearer …"``)
    were written verbatim to ``.agent-runs/<run-id>/memory/*.jsonl``.
    Now we run ``scrub()`` against the canonical pattern list; when a
    secret is detected the record is preserved (timestamp + event +
    run_id still useful for traceability) but the message body is
    replaced with a sentinel and the matched-pattern count goes into
    ``metadata.redacted``.

    The redaction is fail-closed: if ``scrub()`` raises (malformed
    regex), the message is treated as secret-bearing and redacted.
    """
    if not message:
        return message, False, []
    try:
        # Import locally to keep hooks importable when the memory
        # package isn't on PYTHONPATH (e.g. minimal test contexts).
        from memory.redaction import scrub
    except ImportError:
        return message, False, []
    try:
        result = scrub(message)
    except Exception:  # noqa: BLE001 — fail-closed
        return "[REDACTED: scrub raised; treating as secret]", True, ["<scrub-error>"]
    if result.allowed:
        return message, False, []
    return (
        f"[REDACTED: {result.reason}]",
        True,
        list(result.matched_patterns) + list(result.matched_paths),
    )


def record_hook_memory(repo_root: Path, event_name: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    runs = discover_active_runs(repo_root)
    if not runs:
        return
    run = runs[0]
    memory_dir = run.run_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Pass 9 / ENG-008: pre-write scrub against the canonical secret
    # patterns so Bash commands with embedded credentials never reach
    # disk verbatim. Preserves the event row for traceability but
    # replaces the message body with a redaction sentinel.
    sanitized, was_redacted, matched = _redact_message_for_layer_a(message)
    truncated = _truncate(sanitized, MAX_MEMORY_TEXT)

    # Pass 9 / QA-001: auto-populate metadata.type from the event name
    # so the Layer A→B flush filter (which requires metadata.type in
    # allowed_types) actually sees these records as candidates instead
    # of silently dropping them as skipped_no_type. Caller-supplied
    # `metadata["type"]` wins so callers like the decision-ledger or
    # intake skill can override (e.g. "decision", "task_learning").
    merged_metadata: dict[str, Any] = dict(metadata or {})
    if not merged_metadata.get("type"):
        default_type = _EVENT_DEFAULT_TYPE.get(event_name)
        if default_type:
            merged_metadata["type"] = default_type
    if was_redacted:
        merged_metadata["redacted"] = True
        merged_metadata["redacted_match_count"] = len(matched)

    record = {
        "timestamp": _utc_now(),
        "event": event_name,
        "run_id": run.run_id,
        "stage": run.fields.get("current_stage", ""),
        "message": truncated,
        "metadata": merged_metadata,
    }
    target_file = memory_dir / _memory_file_for_event(event_name)
    append_jsonl(target_file, record)
    if target_file.name != "events.jsonl":
        append_jsonl(memory_dir / "events.jsonl", record)
    _write_memory_probe(memory_dir, repo_root, event_name, run)
    _write_handoff(run, memory_dir)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _write_handoff(run: ActiveRun, memory_dir: Path) -> None:
    event_rows = _read_jsonl_tail(memory_dir / "events.jsonl", MAX_HANDOFF_RECORDS)
    open_loop_rows = _read_jsonl_tail(memory_dir / "open_loops.jsonl", MAX_HANDOFF_RECORDS)
    decision_rows = _read_jsonl_tail(memory_dir / "decisions.jsonl", MAX_HANDOFF_RECORDS)
    lines = [
        f"# Agent Pipeline memory - {run.run_id}",
        "",
        f"Generated: {_utc_now()}",
        "",
        "## Run State",
        "",
        f"- stage: {run.fields.get('current_stage', '(unknown)')}",
        f"- next_required_action: {run.fields.get('next_required_action', '(unspecified)')}",
        f"- continuing_to: {run.fields.get('continuing_to', '(unspecified)')}",
        f"- stop_condition: {run.fields.get('stop_condition', '(unset)')}",
        f"- directive_bound: {str(run.directive_bound).lower()}",
        f"- judge_active: {str(run.judge_active).lower()}",
        "",
    ]
    if open_loop_rows:
        lines.extend(["## Open Loops", ""])
        for row in open_loop_rows:
            lines.append(f"- [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    if decision_rows:
        lines.extend(["## Recent Decisions And Warnings", ""])
        for row in decision_rows:
            lines.append(f"- [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    if event_rows:
        lines.extend(["## Recent Hook Memory", ""])
        for row in event_rows:
            lines.append(f"- {row.get('timestamp', '')} [{row.get('event', 'event')}] {row.get('message', '')}")
        lines.append("")
    lines.extend(
        [
            "## Resume Checklist",
            "",
            "- Read the run contract files and memory/*.jsonl before changing scope.",
            "- Re-run relevant policy checks before relying on any remembered approval, warning, or failure state.",
        ]
    )
    (memory_dir / "handoff_current.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_memory_probe(memory_dir: Path, repo_root: Path, event_name: str, run: ActiveRun) -> None:
    with (memory_dir / "memory_probe.log").open("a", encoding="utf-8") as handle:
        handle.write(f"[{_utc_now()}] event={event_name} repo={repo_root} run={run.run_id} stage={run.fields.get('current_stage', '')}\n")


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows[-limit:]


def _memory_file_for_event(event_name: str) -> str:
    if event_name == "UserPromptSubmit":
        return "turns.jsonl"
    if event_name in {"PreToolUse", "PermissionRequest"}:
        return "decisions.jsonl"
    if event_name in {"PostToolUse", "PostToolUseFailure", "Stop"}:
        return "open_loops.jsonl"
    # PreCompact, PostCompact, SubagentStop, SessionEnd, SessionStart all
    # land in events.jsonl. They are bookkeeping rather than decisions or
    # turns, and the handoff pulls from events.jsonl as the catch-all tail.
    return "events.jsonl"


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...[truncated]"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _directive_bound(run_dir: Path) -> bool:
    log = run_dir / "run.log"
    if not log.exists():
        return False
    return "directive-bound | COMPLETE | hash=" in log.read_text(encoding="utf-8-sig", errors="replace")


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def _touches_outside_allowed_paths(event_or_command, run_dir: Path) -> bool:
    """Check if a tool would write to a path outside manifest.allowed_paths.

    Accepts either a Cowork event dict (preferred — extracts file_path from
    structured tool_input for Write/Edit/MultiEdit/NotebookEdit) or a bare
    command string (legacy callers that already serialized to text).
    Returns True only when ALL extracted candidate paths are outside the
    allowed set and the allowed set is non-empty.
    """
    manifest = run_dir / "manifest.yaml"
    if not manifest.exists():
        return False
    allowed = _manifest_list(manifest, "allowed_paths")
    if not allowed:
        return False

    candidates = _extract_write_paths(event_or_command)
    if not candidates:
        return False

    for raw in candidates:
        normalized = raw.replace("\\", "/").lstrip("./")
        if not any(
            normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/")
            for item in allowed
        ):
            return True
    return False


# Explicit allowlist of MCP write tools and the tool_input fields where
# they carry their target file path. Generic recursive path extraction
# was rejected during audit synthesis (Pass 7 / Cluster G) because it
# false-positives on every MCP that happens to have a string field
# named "path" or "destination" — including remote APIs (mcp__github__*,
# mcp__slack__*, ...) that never touch the local filesystem. The
# audit-locked decision: explicit allowlist of LOCAL-filesystem write
# tools only.
#
# Each entry maps a compiled regex matching the tool_name to a tuple of
# tool_input field names that hold local file paths. Add new entries
# here as new local-filesystem MCPs are adopted by operators.
#
# Intentionally NOT in the allowlist:
#   * `mcp__github__create_or_update_file`, `mcp__github__push_files` —
#     push to GitHub via API, do NOT modify the local working tree.
#     Remote pushes are gated by EXTERNAL_OR_RELEASE_PATTERNS, not by
#     scope-lock allowed_paths.
#   * `mcp__*__send_message`, `mcp__*__post_*` — outbound network calls,
#     no local write surface.
MCP_LOCAL_WRITE_TOOL_RULES: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"^mcp__.+__create_file$"), ("path", "file_path")),
    (re.compile(r"^mcp__.+__copy_file$"), ("destination", "destination_path", "to", "target")),
    (re.compile(r"^mcp__.+__write_file$"), ("path", "file_path")),
    (re.compile(r"^mcp__.+__upload_file$"), ("path", "file_path", "destination")),
    (re.compile(r"^mcp__.+__save_profile$"), ("path", "profile_path")),
    # PDF tools: fill_pdf, merge_pdfs, reorder_pdf_pages, etc. produce
    # output files locally per tool_input.output (or output_path).
    (re.compile(r"^mcp__.+__fill_pdf$"), ("output", "output_path")),
    (re.compile(r"^mcp__.+__merge_pdfs$"), ("output", "output_path")),
    (re.compile(r"^mcp__.+__split_pdf$"), ("output", "output_path", "output_dir")),
    (re.compile(r"^mcp__.+__bulk_fill_from_csv$"), ("output", "output_path", "output_dir")),
)


def _extract_mcp_local_write_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Apply the explicit MCP-local-write allowlist to a tool_input dict.

    Returns every field-value from the allowlist's per-tool field list
    that looks like a non-empty string path. Unknown MCP tools return
    [] — those go through the rest of `_extract_write_paths`'s
    extraction path (which finds nothing for MCPs that aren't in the
    allowlist, by design).
    """
    if not tool_name or not isinstance(tool_input, dict):
        return []
    for pattern, fields in MCP_LOCAL_WRITE_TOOL_RULES:
        if pattern.match(tool_name):
            paths: list[str] = []
            for field_name in fields:
                value = tool_input.get(field_name)
                if isinstance(value, str) and value:
                    paths.append(value)
            return paths
    return []


def _extract_write_paths(event_or_command) -> list[str]:
    """Return every file path a tool call would write to.

    For Cowork event dicts: pulls `tool_input.file_path` (Write / Edit /
    NotebookEdit), `tool_input.edits[].file_path` (MultiEdit), and falls
    back to shell-command parsing for Bash. Also consults the explicit
    MCP allowlist at ``MCP_LOCAL_WRITE_TOOL_RULES`` for local-filesystem
    write MCPs (Pass 7 / Cluster G). For bare strings: only the
    shell-command path.
    """
    paths: list[str] = []
    if isinstance(event_or_command, dict):
        tool_input = event_or_command.get("tool_input")
        if isinstance(tool_input, dict):
            file_path = tool_input.get("file_path")
            if isinstance(file_path, str) and file_path:
                paths.append(file_path)
            # MultiEdit: edits list with per-entry file_path
            edits = tool_input.get("edits")
            if isinstance(edits, list):
                for edit in edits:
                    if isinstance(edit, dict):
                        fp = edit.get("file_path")
                        if isinstance(fp, str) and fp:
                            paths.append(fp)
            # NotebookEdit may use notebook_path
            nb_path = tool_input.get("notebook_path")
            if isinstance(nb_path, str) and nb_path:
                paths.append(nb_path)
            # Allowlisted MCP local-write tools (Pass 7 / Cluster G).
            tool_name = event_or_command.get("tool_name") or ""
            if isinstance(tool_name, str):
                paths.extend(_extract_mcp_local_write_paths(tool_name, tool_input))
        # Always also try the shell command if present
        command_text = tool_command(event_or_command)
    else:
        command_text = str(event_or_command or "")

    legacy = _extract_write_path(command_text)
    if legacy:
        paths.append(legacy)
    # de-dupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _manifest_list(path: Path, key: str) -> list[str]:
    """Collect `- ...` list items for a YAML key, terminating cleanly at the
    next sibling key.

    Earlier implementation walked until an unindented line, which spilled
    across sibling keys in indented YAML (e.g. allowed_paths sitting under
    pipeline_run: would absorb required_gates items). This version tracks
    the indent of the matched key and terminates as soon as a non-list line
    appears at or shallower than that indent.
    """
    values: list[str] = []
    in_key = False
    key_indent = -1
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = len(raw) - len(raw.lstrip(" \t"))
        if not in_key:
            if stripped.startswith(f"{key}:"):
                in_key = True
                key_indent = line_indent
            continue
        # In list-collection mode for `key`
        if stripped.startswith("- "):
            # Require strictly deeper indent than the key itself
            if line_indent > key_indent:
                values.append(stripped[2:].strip().strip("\"'"))
            else:
                # A dash at <= key_indent means we left this key's subtree
                break
            continue
        # Any other content terminates if it is at or shallower than the key indent
        if line_indent <= key_indent:
            break
    return values


def _extract_write_path(command: str) -> str:
    match = re.search(r"(?:Set-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item)\s+(?:-LiteralPath\s+|-Path\s+)?['\"]?([^'\"\s]+)", command, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"(?:>|>>)\s*['\"]?([^'\"\s]+)", command)
    if match:
        return match.group(1)
    return ""
