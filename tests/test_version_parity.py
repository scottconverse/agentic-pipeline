# SPDX-License-Identifier: Apache-2.0
"""Version-parity guard (audit DOC-001 / UX-001 / UX-008 / QA-011).

The plugin ships `check_release_docs_consistency.py` for *consumer* projects but
historically did not apply that discipline to itself: at the v3.0.0 audit the
manifests said 3.0.0 while the README/manual/landing page said v2.2.x and the
script `--version` strings said 2.0.0/1.2.1. This test pins every version-bearing
surface to the canonical `plugin.json` version so the skew cannot silently recur.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _plugin_version() -> str:
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    return data["version"]


def test_manifests_agree_on_version() -> None:
    version = _plugin_version()
    mkt = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert mkt["metadata"]["version"] == version
    assert mkt["plugins"][0]["version"] == version


def test_all_script_version_strings_match_plugin() -> None:
    version = _plugin_version()
    pat = re.compile(r'version="agent-pipeline-claude ([0-9][^"]*)"')
    stale = []
    for py in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for m in pat.finditer(py.read_text(encoding="utf-8")):
            if m.group(1) != version:
                stale.append(f"{py.name}: {m.group(1)}")
    assert not stale, f"--version strings stale vs plugin.json {version}: {stale}"


def test_docs_front_matter_matches_plugin_version() -> None:
    version = _plugin_version()
    checks = {
        "README.md": rf"Current release: v{re.escape(version)}\b",
        "USER-MANUAL.md": rf"\*\*Version:\*\* {re.escape(version)}\b",
        "ARCHITECTURE.md": rf"Current version: v{re.escape(version)}\b",
        "docs/index.html": rf"Claude Code plugin . v{re.escape(version)}\b",
    }
    missing = []
    for rel, pattern in checks.items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if not re.search(pattern, text):
            missing.append(rel)
    assert not missing, f"these docs don't advertise plugin version v{version}: {missing}"


def test_no_deprecated_autonomous_skills_in_payload() -> None:
    """DOC-002 / UX-003: the deprecated *-autonomous skills are removed."""
    for gone in ("run-autonomous", "grant-autonomous"):
        assert not (REPO_ROOT / "skills" / gone).exists(), f"{gone} should be removed"
