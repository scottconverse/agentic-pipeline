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
# Pass 11 regressions: doc staleness sweep
# ---------------------------------------------------------------------------
#
# Pre-Pass-11 the user-facing docs (README, USER-MANUAL, ARCHITECTURE,
# tests/README, docs/VERIFICATION, docs/index.html) referenced the v1.x
# version literals and the retired "Reply APPROVE" chat ceremony.
# Operators reading the docs got a different mental model than what the
# code actually did. These tests pin the post-Pass-11 invariants.


def test_readme_does_not_instruct_reply_approve_to_start():
    """README's quick-start must not say `Reply APPROVE to start`. The
    real flow is: read the orientation summary in chat, then click the
    APPROVE modal that follows (Pass 5 / Cluster E aligns pipeline-init
    + run skill on modal gates)."""
    text = _read(REPO_ROOT / "README.md")
    assert "Reply APPROVE to start" not in text


def test_readme_upgrade_instruction_targets_v2():
    """README's migration section must direct upgraders at the current
    tag, not the stale v1.1.0 instruction."""
    text = _read(REPO_ROOT / "README.md")
    assert "git checkout v1.1.0" not in text, (
        "README still tells operators to `git checkout v1.1.0` — stale"
    )
    assert "git checkout v2.0.0" in text


def test_user_manual_upgrade_instruction_targets_v2():
    """USER-MANUAL's upgrade snippet must match the README upgrade
    snippet (same instruction in both surfaces)."""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    assert "git checkout v1.1.0" not in text
    assert "git checkout v2.0.0" in text


def test_architecture_current_version_is_v2():
    """ARCHITECTURE.md must declare the current version as v2.0+. The
    v1.x stage architecture is still described below the version line
    (v2.0 rides on top of it); only the active-version label updates."""
    text = _read(REPO_ROOT / "ARCHITECTURE.md")
    assert "**Current version: v1.1.0.**" not in text, (
        "ARCHITECTURE still claims Current version: v1.1.0"
    )
    assert "**Current version: v2.0.0.**" in text


def test_tests_readme_version_label_is_v2():
    text = _read(REPO_ROOT / "tests" / "README.md")
    assert "v1.1.0+" not in text
    assert "v2.0.0+" in text


def test_landing_page_version_badge_is_v2():
    """docs/index.html badge / eyebrow must show v2.0.x, not v1.1.0."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert ">v1.1.0<" not in text, "landing page badge still says v1.1.0"
    assert "v2.0.0" in text


def test_manifest_template_documents_v2_optional_gates():
    """manifest-template.yaml must mention the v2.0 conditional gates
    (directive_bound / scope_lock_authority / execute_readiness) in the
    required_gates comment block so operators know they exist (ENG-010)."""
    for path in (
        REPO_ROOT / "pipelines" / "manifest-template.yaml",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload"
        / "pipelines" / "manifest-template.yaml",
    ):
        text = _read(path)
        for needle in ("directive_bound", "scope_lock_authority", "execute_readiness"):
            assert needle in text, (
                f"{path.name} missing v2.0 gate hint `{needle}`"
            )


def test_directive_template_uses_placeholder_author_and_reference():
    """directive-template.yaml ships placeholder strings the operator
    must replace before binding. Pre-Pass-11 the template hard-coded
    `Scott Converse` and `docs/design/example.md`, which would have
    been baked into any directive copied from it. Now they're explicit
    placeholders."""
    for path in (
        REPO_ROOT / "pipelines" / "directive-template.yaml",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload"
        / "pipelines" / "directive-template.yaml",
    ):
        text = _read(path)
        assert "Scott Converse" not in text, (
            f"{path.name} still hard-codes `Scott Converse` as the author"
        )
        assert "docs/design/example.md" not in text, (
            f"{path.name} still hard-codes `docs/design/example.md` as authority.reference"
        )
        assert "<your-name-or-team>" in text
        assert "<path/to/design-doc-or-pr-or-issue>" in text


def test_check_manifest_schema_error_does_not_mention_chat_approve():
    """check_manifest_schema's gate_policy suggestion string must not
    cite the retired chat-APPROVE ceremony. Pre-Pass-11 the suggestion
    told operators `three gates require chat-APPROVE` which contradicted
    the v1.3.0 modal redesign."""
    text = _read(REPO_ROOT / "scripts" / "check_manifest_schema.py")
    assert "chat-APPROVE" not in text, (
        "check_manifest_schema still cites chat-APPROVE in an error string"
    )


# ---------------------------------------------------------------------------
# Pass 11b regressions: chat-APPROVE residue sweep (post-Pass-11 audit-lite)
# ---------------------------------------------------------------------------
#
# End-sprint audit-lite caught operator-facing chat-APPROVE residue that
# Pass 11 missed: docs/index.html stage-flow + "Three human gates"
# gate-cards + first-use copy still said `chat APPROVE`; USER-MANUAL
# Glossary + Migration sections still described gates as chat messages;
# the pipeline-payload mirror of check_manifest_schema.py still had the
# pre-Pass-11 error string with `chat-APPROVE` (Pass 11 fixed only the
# top-level). Same pattern-fan-out failure mode Pass 8a closed for
# find_repo_root. Pass 11b closes the doc-surface fan-out.


def test_landing_page_stage_flow_uses_modal_not_chat_approve():
    """docs/index.html stage-flow diagram (the <pre class='stage-flow'>
    block) must show `modal APPROVE` for the three gate annotations,
    not `chat APPROVE`."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "chat APPROVE" not in text, (
        "landing page still labels gates as `chat APPROVE`; should be `modal APPROVE`"
    )


def test_landing_page_three_gates_heading_says_modal():
    """The <h2> for the three-gates section must name modal gates, and
    the gate-card body copy must not say `Reply APPROVE`."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "Three human gates, in chat" not in text, (
        "landing page heading still says `Three human gates, in chat` — should be modal-labelled"
    )
    assert "Reply " not in text or "Reply APPROVE" not in text
    assert "modal" in text.lower(), "landing page should describe gates as modal"


def test_landing_page_does_not_claim_gates_are_chat_messages_not_modal():
    """The pre-Pass-11b copy declared `Gates are chat messages, not
    modal popups.` That's the inverse of the truth."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "Gates are chat messages, not modal popups" not in text


def test_landing_page_first_use_does_not_say_approve_in_chat():
    """`You approve in chat.` in the First-use section is wrong post-v1.3.0."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "You approve in chat" not in text


def test_user_manual_glossary_manifest_uses_modal_language():
    """USER-MANUAL Glossary `Manifest` entry must describe the gate as
    a modal, not `gated on chat APPROVE`."""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    assert "gated on chat APPROVE" not in text


def test_user_manual_migration_section_describes_modal_gates():
    """USER-MANUAL Migration from v0.5.x must describe modal gates, not
    `chat messages (APPROVE / REPLAN / BLOCK), not modal popups`."""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    assert "chat messages (APPROVE / REPLAN / BLOCK), not modal popups" not in text


def test_check_manifest_schema_mirror_matches_top_level_chat_approve_removal():
    """Pass 11 fixed the chat-APPROVE error string in the top-level
    check_manifest_schema.py but left the pipeline-payload mirror
    unchanged. Pass 11b syncs the mirror. This test pins lockstep so
    a future fix can't drift the two sides apart again."""
    top_level = _read(REPO_ROOT / "scripts" / "check_manifest_schema.py")
    mirror = _read(
        REPO_ROOT
        / "skills" / "pipeline-init" / "references" / "pipeline-payload" / "scripts"
        / "check_manifest_schema.py"
    )
    # Both should be free of the pre-Pass-11 string.
    assert "chat-APPROVE" not in top_level, "top-level still cites chat-APPROVE"
    assert "chat-APPROVE" not in mirror, (
        "pipeline-payload mirror of check_manifest_schema.py still cites "
        "chat-APPROVE — Pass 11 missed this; Pass 11b should close it."
    )


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
