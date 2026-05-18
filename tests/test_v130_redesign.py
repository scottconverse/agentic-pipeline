# SPDX-License-Identifier: Apache-2.0
"""v1.3.0 redesign contract tests.

These tests pin the v1.3.0 surface so a future change can't silently
re-introduce the v1.2.x grant + autonomous-mode flow.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pipeline yaml hygiene
# ---------------------------------------------------------------------------

def test_no_autonomous_skip_chat_in_pipeline_yamls():
    """No pipeline yaml carries `autonomous_skip_chat: true` in v1.3.0."""
    for yml in (REPO_ROOT / "pipelines").glob("*.yaml"):
        text = _read(yml)
        assert "autonomous_skip_chat: true" not in text, (
            f"{yml.name} still contains autonomous_skip_chat: true — "
            f"v1.3.0 removed this flag because gates are modal."
        )


def test_payload_pipeline_yamls_clean():
    """Same check on the pipeline-init payload."""
    payload = REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "pipelines"
    for yml in payload.glob("*.yaml"):
        text = _read(yml)
        assert "autonomous_skip_chat: true" not in text, (
            f"payload/{yml.name} still contains autonomous_skip_chat: true"
        )


def test_manifest_template_has_no_gate_policy_field():
    """manifest-template.yaml must not ship gate_policy: as a field."""
    for path in [
        REPO_ROOT / "pipelines" / "manifest-template.yaml",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "pipelines" / "manifest-template.yaml",
    ]:
        text = _read(path)
        # The field would look like `  gate_policy: human` or `  gate_policy: autonomous`
        # at top-of-line under the pipeline_run: block. Comments mentioning the
        # historical field are OK.
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("gate_policy:"), (
                f"{path}: still declares gate_policy: as a manifest field — v1.3.0 removed it."
            )


def test_manifest_template_has_no_autonomous_grant_field():
    """manifest-template.yaml must not ship autonomous_grant: as a field."""
    for path in [
        REPO_ROOT / "pipelines" / "manifest-template.yaml",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "pipelines" / "manifest-template.yaml",
    ]:
        text = _read(path)
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("autonomous_grant:"), (
                f"{path}: still declares autonomous_grant: as a manifest field — v1.3.0 removed it."
            )


# ---------------------------------------------------------------------------
# Role files: no autonomous-mode awareness sections
# ---------------------------------------------------------------------------

def test_no_autonomous_mode_awareness_in_roles():
    """The autonomous-mode awareness sections in role files are gone."""
    for role in (REPO_ROOT / "pipelines" / "roles").glob("*.md"):
        text = _read(role)
        assert "## Autonomous-mode awareness" not in text, (
            f"{role.name} still has a `## Autonomous-mode awareness` section."
        )


def test_no_autonomous_mode_awareness_in_payload_roles():
    """Same check on the payload role files."""
    payload_roles = (
        REPO_ROOT
        / "skills"
        / "pipeline-init"
        / "references"
        / "pipeline-payload"
        / "pipelines"
        / "roles"
    )
    for role in payload_roles.glob("*.md"):
        text = _read(role)
        assert "## Autonomous-mode awareness" not in text, (
            f"payload/{role.name} still has a `## Autonomous-mode awareness` section."
        )


# ---------------------------------------------------------------------------
# Skills: deprecation shims
# ---------------------------------------------------------------------------

def test_run_autonomous_is_deprecation_shim():
    skill_md = REPO_ROOT / "skills" / "run-autonomous" / "SKILL.md"
    text = _read(skill_md)
    assert "Deprecated" in text or "deprecated" in text
    assert "v1.3.0" in text
    # Must redirect users to /run
    assert "/agent-pipeline-claude:run" in text


def test_grant_autonomous_is_deprecation_shim():
    skill_md = REPO_ROOT / "skills" / "grant-autonomous" / "SKILL.md"
    text = _read(skill_md)
    assert "Deprecated" in text or "deprecated" in text
    assert "v1.3.0" in text


# ---------------------------------------------------------------------------
# Run skill: uses AskUserQuestion not chat-APPROVE ceremony
# ---------------------------------------------------------------------------

def test_run_skill_references_askuserquestion():
    """SKILL.md of /run must reference AskUserQuestion as the gate tool."""
    text = _read(REPO_ROOT / "skills" / "run" / "SKILL.md")
    assert "AskUserQuestion" in text, (
        "v1.3.0 run skill must use AskUserQuestion for the three human gates."
    )


def test_run_procedure_uses_modal_gates():
    """references/run.md must describe modal gates, not chat-APPROVE ceremony."""
    text = _read(REPO_ROOT / "skills" / "run" / "references" / "run.md")
    assert "AskUserQuestion" in text
    # The v1.2.x hard rule that BANNED AskUserQuestion must be gone.
    assert "Never invoke `AskUserQuestion`" not in text
    assert "never substitute `AskUserQuestion`" not in text.lower()


def test_run_skill_does_not_require_grant():
    """SKILL.md must not require a grant file for autonomous flow."""
    text = _read(REPO_ROOT / "skills" / "run" / "SKILL.md")
    # The v1.2.1 SKILL.md had "v1.2.1+ Autonomous mode procedure" section.
    assert "Autonomous mode procedure" not in text
    # Auto-promote should be cited as the path to hands-off.
    assert "auto-promote" in text.lower() or "auto_promote" in text


# ---------------------------------------------------------------------------
# Pipeline-init skill: also uses AskUserQuestion (audit Pass 5 / Cluster E)
# ---------------------------------------------------------------------------
#
# v1.3.0 retired the chat-`APPROVE` ceremony for the three run-time gates
# (manifest, plan, manager). The pipeline-init skill missed that
# bandwagon — it kept telling Claude to "Render the orientation summary
# as a plain chat message — do not use AskUserQuestion for the APPROVE
# gate." Pass 5 aligns pipeline-init with the v1.3.0 design: the chat
# message stays (it's informational), but the decision goes through a
# modal prompt.


def test_pipeline_init_skill_references_askuserquestion():
    """SKILL.md of /pipeline-init must reference AskUserQuestion for the
    gate flow (Pass 5 / Cluster E)."""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "SKILL.md")
    assert "AskUserQuestion" in text, (
        "Pass 5 pipeline-init skill must use AskUserQuestion for the "
        "approve / wait / cancel decision."
    )


def test_pipeline_init_skill_does_not_ban_askuserquestion():
    """The pre-Pass-5 SKILL.md had the explicit line `do not use
    AskUserQuestion for the APPROVE gate`. That instruction is gone."""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "SKILL.md")
    forbidden = (
        "do not use `AskUserQuestion` for the APPROVE gate",
        "do not use AskUserQuestion for the APPROVE gate",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"pipeline-init SKILL.md still contains the pre-Pass-5 ban "
            f"on AskUserQuestion ({needle!r}). v1.3.0 retired chat-APPROVE."
        )


def test_pipeline_init_procedure_uses_modal_gates():
    """references/pipeline-init.md must instruct Claude to invoke
    AskUserQuestion for the scaffold / re-init / greenfield gates."""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-init.md")
    assert text.count("AskUserQuestion") >= 3, (
        "pipeline-init.md should reference AskUserQuestion at least at "
        "the scaffold gate, the greenfield SPEC.md gate, and the "
        "re-init refresh gate."
    )
    # The pre-Pass-5 free-text gate instructions must be gone (no
    # `Reply with a, b, c, or d` or `Reply APPROVE` in chat).
    assert "Reply with a, b, c, or d" not in text
    assert "Reply `APPROVE` to scaffold" not in text


# ---------------------------------------------------------------------------
# Version pin
# ---------------------------------------------------------------------------

def test_plugin_version_is_redesign_or_later():
    """Pins the v1.3 redesign surface or its v2.0+ successor. v2.0.0 carries
    forward the modal-gate invariants from v1.3.x (the heavier-hand redesign
    adds hooks, Mem0, and directive contracts on top, but does not regress
    chat-APPROVE or grant-based autonomy). Uses a semver-shape regex so
    patch releases don't rewrite this test but malformed strings still fail."""
    import json
    import re
    plugin = json.loads(_read(REPO_ROOT / ".claude-plugin" / "plugin.json"))
    version = plugin["version"]
    assert re.fullmatch(r"(?:1\.3|2\.\d+)\.\d+(?:[-+].+)?", version), (
        f"plugin.json version is {version!r}; expected 1.3.<patch> or "
        "2.<minor>.<patch> (optionally with pre-release/build suffix). "
        "If the redesign is being reverted, update this test deliberately."
    )


def test_changelog_has_v130_entry():
    text = _read(REPO_ROOT / "CHANGELOG.md")
    assert "## [1.3.0]" in text
    # Must reference the redesign rationale
    assert "modal" in text.lower() or "AskUserQuestion" in text


# ---------------------------------------------------------------------------
# Backward compat: stubs return zero so existing yamls still work
# ---------------------------------------------------------------------------

def test_check_autonomous_mode_is_noop():
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_autonomous_mode.py")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "HUMAN-MODE" in r.stdout


def test_check_autonomous_compliance_is_noop():
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_autonomous_compliance.py")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "NO-OP" in r.stdout
