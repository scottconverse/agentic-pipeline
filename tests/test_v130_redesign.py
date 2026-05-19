# SPDX-License-Identifier: Apache-2.0
"""v1.3.0 redesign contract tests.

These tests pin the v1.3.0 surface so a future change can't silently
re-introduce the v1.2.x grant + autonomous-mode flow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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
    assert "git checkout v2.1.0" in text


def test_user_manual_upgrade_instruction_targets_v2():
    """USER-MANUAL's upgrade snippet must match the README upgrade
    snippet (same instruction in both surfaces)."""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    assert "git checkout v1.1.0" not in text
    assert "git checkout v2.1.0" in text


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


def test_landing_page_problem_section_does_not_say_approve_in_chat():
    """The Problem section's value pitch (`Every run starts with a
    manifest you approve in chat.`) was missed by Pass 11b's first pass.
    The lowercase 'approve in chat' phrase must be replaced with the
    modal description anywhere it appears in operator-facing copy."""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "approve in chat" not in text, (
        "landing page still has 'approve in chat' operator-facing copy"
    )


# ---------------------------------------------------------------------------
# Pass 11d regressions: case-insensitive sweep + B1/B2 + role + diagrams
# ---------------------------------------------------------------------------
#
# Pass 11/11b/11c each closed a residue, but each used case-sensitive or
# narrow phrase matching, so the next iteration of the verifier still
# found more (`APPROVE in chat`, `operator must type APPROVE`, the
# manifest-drafter role file, ARCHITECTURE.md Mermaid + sequence
# diagrams). Pass 11d does a case-INSENSITIVE sweep so the family of
# variants can't slip past anymore.

import re as _re

# Operator-facing surfaces. These are the docs an operator reads BEFORE
# running anything — they must describe modal gates, never chat-APPROVE
# instructions. Historical mentions ("v1.3.0 retired chat-APPROVE",
# "the chat-APPROVE ceremony was the failure mode") are allowed
# inside CHANGELOG.md and skill SKILL.md files because they describe
# what was retired, not how to use the current system.
_OPERATOR_FACING_FILES = (
    "README.md",
    "USER-MANUAL.md",
    "ARCHITECTURE.md",
    "docs/index.html",
    "docs/module-release-handbook.md",
    "pipelines/roles/manifest-drafter.md",
    "skills/pipeline-init/references/pipeline-payload/pipelines/roles/manifest-drafter.md",
)

# Pattern family for the retired chat-APPROVE ceremony. Each pattern is
# matched case-insensitively. The regex is intentionally narrow — it
# matches operator INSTRUCTIONS (imperatives or descriptions of what
# the operator does today), not historical descriptions of what v1.3.0
# retired.
_INSTRUCTIONAL_CHAT_APPROVE_PATTERNS = (
    # "APPROVE in chat" / "approve in chat" / etc.
    _re.compile(r"\bapprove\s+in\s+chat\b", _re.IGNORECASE),
    # "chat APPROVE" / "chat-APPROVE" as a noun describing the action.
    # The historical mentions in CHANGELOG / SKILL.md describe what was
    # retired, so they're scoped out of this test's file list.
    _re.compile(r"\bchat[- ]message\s+APPROVE\b", _re.IGNORECASE),
    # "Reply APPROVE" / "reply with APPROVE" as instructions.
    _re.compile(r"\breply\s+(?:with\s+)?`?APPROVE`?\s+(?:in\s+chat|to\b)", _re.IGNORECASE),
    # "type APPROVE" as an instruction.
    _re.compile(r"\btype\s+`?APPROVE`?\b", _re.IGNORECASE),
    # "operator must type APPROVE" as a definition.
    _re.compile(r"\boperator\s+must\s+type\s+APPROVE\b", _re.IGNORECASE),
    # "you review YAML in chat and APPROVE" as a procedure.
    _re.compile(r"\bin\s+chat\s+and\s+APPROVE\b", _re.IGNORECASE),
)


# Surrounding-line tokens that mark a match as HISTORICAL (allowed).
# The patterns above are imperatives or procedural descriptions, but
# the same phrasing legitimately appears in lines that EXPLAIN what
# v1.3.0 retired — those are honest history, not operator instructions.
_HISTORY_MARKERS = (
    "retired",
    "v1.3.0 replaced",
    "v0.5.x",
    "ceremony",  # "the chat-APPROVE ceremony was the failure mode"
    "no longer",
    "previously",
    "pre-pass-",
    "was retired",
    "was wrong",
    "now wrong",
)


def _is_historical_line(line: str) -> bool:
    """Return True if the line describes the retired ceremony rather
    than instructing the operator to use it. The test scoping is
    intentional: historical mentions ARE allowed in operator-facing
    docs (they explain why the modal exists), and only instructional
    or procedural uses count as residue."""
    lowered = line.lower()
    return any(marker in lowered for marker in _HISTORY_MARKERS)


@pytest.mark.parametrize("filename", _OPERATOR_FACING_FILES)
def test_no_chat_approve_instructional_residue(filename: str) -> None:
    """Pass 11d case-insensitive sweep. Operator-facing docs must not
    instruct the operator to APPROVE via chat. Three prior passes
    (11, 11b, 11c) closed residues one phrasing at a time; this test
    catches the family with a case-insensitive regex sweep so the
    next variant can't sneak in. Historical mentions (lines containing
    "retired" / "v1.3.0 replaced" / "ceremony" etc.) are intentionally
    allowed — they describe what was retired, not what to do now."""
    text = _read(REPO_ROOT / filename)
    lines = text.splitlines()
    found: list[str] = []
    for pattern in _INSTRUCTIONAL_CHAT_APPROVE_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            line_text = lines[line_no - 1] if line_no - 1 < len(lines) else ""
            if _is_historical_line(line_text):
                continue  # honest history; not an instruction
            found.append(f"  {filename}:{line_no} → {match.group(0)!r}")
    assert not found, (
        f"{filename} contains operator-instructional chat-APPROVE residue:\n"
        + "\n".join(found)
        + "\n\nThese should describe the modal gate (`AskUserQuestion`), not "
        "the retired v0.5.x chat ceremony. v1.3.0 retired chat-APPROVE; "
        "v2.0 keeps the modal flow. (Lines mentioning 'retired' / 'v0.5.x' / "
        "'ceremony' are scoped out — they're honest history.)"
    )


def test_readme_tagline_describes_modal():
    """B1 from end-sprint audit-lite verifier: README.md:5 used to say
    'asks you to APPROVE in chat'. Must describe the modal."""
    text = _read(REPO_ROOT / "README.md")
    assert "APPROVE in chat" not in text  # case-sensitive check for the specific phrase
    assert "AskUserQuestion" in text, "README tagline should name AskUserQuestion"


def test_architecture_glossary_gate_describes_modal():
    """B2 from end-sprint audit-lite verifier: ARCHITECTURE.md:856 used
    to say 'operator must type APPROVE'. Must describe the modal."""
    text = _read(REPO_ROOT / "ARCHITECTURE.md")
    assert "operator must type APPROVE" not in text
    # Sanity: confirm the modal description landed.
    assert "operator clicks APPROVE / REPLAN / BLOCK" in text or \
           "AskUserQuestion" in text


def test_manifest_drafter_role_describes_modal():
    """Pass 11d found that manifest-drafter.md (and its mirror) still
    instructed the drafter that the operator 'replies APPROVE'. The
    drafter must understand the operator clicks a modal option."""
    for path in (
        REPO_ROOT / "pipelines" / "roles" / "manifest-drafter.md",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload"
        / "pipelines" / "roles" / "manifest-drafter.md",
    ):
        text = _read(path)
        # The role file must not instruct the drafter to expect chat-text
        # responses from the operator.
        assert "replies `APPROVE`" not in text, (
            f"{path.name} still says the operator replies APPROVE in chat"
        )
        # Must name the modal mechanism.
        assert "AskUserQuestion" in text or "modal" in text.lower(), (
            f"{path.name} must describe the modal gate"
        )


def test_architecture_diagrams_describe_modal_gates():
    """ARCHITECTURE.md Mermaid flowchart (line ~189) and sequence
    diagram (line ~298) used to label gates as `chat-message APPROVE`.
    Update to `modal AskUserQuestion` or equivalent so the diagram
    matches code."""
    text = _read(REPO_ROOT / "ARCHITECTURE.md")
    assert "chat-message APPROVE" not in text, (
        "ARCHITECTURE.md diagram still labels gates as chat-message APPROVE"
    )


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
