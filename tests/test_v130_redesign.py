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
# Run skill: uses chat-based gate keyword grammar (v2.2.1 reversal)
# ---------------------------------------------------------------------------

# v2.2.1: the v1.3.0 → v2.1.0 modal `AskUserQuestion` design was reversed
# after the operator-UX failure (Cowork's modal overlay hid the chat
# context the operator needed at gate-decision time). Gates are now chat
# prompts with deterministic first-token keyword parsing. These tests
# pin the chat-gate contract.


def test_run_skill_uses_chat_gates():
    """v2.2.1: SKILL.md of /run must instruct chat-based gates with the
    explicit keyword grammar, NOT modal AskUserQuestion as the gate tool.
    (Formerly test_run_skill_references_askuserquestion under v1.3.0.)"""
    text = _read(REPO_ROOT / "skills" / "run" / "SKILL.md")
    # The chat keyword grammar must be named so the orchestrator's tool
    # mapping section is unambiguous.
    assert "chat gate" in text.lower(), (
        "v2.2.1 run skill must instruct chat-based gates in its tool mapping."
    )
    # The five recognized keywords must be cited so the orchestrator can
    # reproduce them in the gate prompts.
    for keyword in ("APPROVE", "REVISE", "REPLAN", "BLOCK", "VIEW"):
        assert keyword in text, (
            "v2.2.1 run skill must name the recognized chat-gate keyword "
            f"{keyword!r} so the orchestrator's gate prompts are consistent."
        )
    # The skill must explicitly forbid firing AskUserQuestion for gates
    # (the modal-budget hook denies it; documenting the rule here keeps
    # the contract visible to operators reading the skill).
    assert "modal-budget hook" in text.lower() or "MODAL_BUDGET_EXCEEDED" in text, (
        "v2.2.1 run skill must reference the modal-budget hook as the "
        "structural backstop on AskUserQuestion."
    )


def test_run_procedure_uses_chat_gates():
    """v2.2.1: references/run.md must describe chat-based gates with
    deterministic first-token keyword parsing. The v0.5.x chat-APPROVE
    ceremony is the surface that returns under v2.2.1; the modal-budget
    hook + explicit keyword grammar + no-parse re-print are the
    structural backstops that the original ceremony lacked.
    (Formerly test_run_procedure_uses_modal_gates under v1.3.0.)"""
    text = _read(REPO_ROOT / "skills" / "run" / "references" / "run.md")
    # The chat keyword grammar must be present in the gate sections.
    for keyword in ("APPROVE", "REVISE", "REPLAN", "BLOCK", "VIEW"):
        assert keyword in text, (
            f"v2.2.1 run.md must include the chat-gate keyword {keyword!r}."
        )
    # First-token parsing must be documented so the orchestrator parses
    # operator replies deterministically.
    assert "first non-whitespace token" in text.lower() or \
           "first-token" in text.lower(), (
        "v2.2.1 run.md must document deterministic first-token keyword "
        "parsing for the chat gates."
    )
    # Case-insensitivity must be explicit so operators can type either case.
    assert "case-insensitive" in text.lower(), (
        "v2.2.1 run.md must document case-insensitive keyword parsing."
    )
    # The v1.2.x hard rule that BANNED AskUserQuestion ALSO must still be
    # gone (preserved from the v1.3.0 invariant — the BAN was a different
    # failure mode, distinct from the v2.2.1 modal-budget hook deny).
    assert "Never invoke `AskUserQuestion`" not in text
    assert "never substitute `AskUserQuestion`" not in text.lower()


# ---------------------------------------------------------------------------
# v3.0.0 WS-3: resident-context execution — run.md Step 7.4 builds the
# subagent context block via a window-gated branch. Off 1M it concatenates
# every prior artifact (current behaviour); on a 1M model it injects an
# artifact index and has the subagent read on demand. The critic keeps the
# full block at every window so its hostile cold read is never starved.
# ---------------------------------------------------------------------------


def test_run_context_block_has_lean_path_for_1m():
    """run.md must offer a lean run-context path that injects an artifact
    index and reads on demand instead of pasting every prior artifact."""
    text = _read(REPO_ROOT / "skills" / "run" / "references" / "run.md")
    assert "Full block" in text and "Lean block" in text, (
        "run.md Step 7.4 must split the run-context block into a full path "
        "(default) and a lean path (1M only)."
    )
    assert "artifact index" in text, (
        "the lean path must inject an artifact index rather than every "
        "prior artifact's full text."
    )
    assert "read on demand" in text and "resident" in text, (
        "the lean path must instruct the subagent to read artifacts on "
        "demand, keeping the run context resident rather than inline."
    )


def test_run_context_lean_path_gates_on_detect_context_window_model_set():
    """The 1M gating clause must name the same model set as the hook helper,
    so run.md and detect_context_window share one source of truth."""
    text = _read(REPO_ROOT / "skills" / "run" / "references" / "run.md")
    assert "1M-context model" in text, (
        "run.md must gate the lean path on being a 1M-context model."
    )
    assert "detect_context_window" in text, (
        "run.md must cross-reference hooks/hook_utils.py:detect_context_window "
        "as the canonical model->window mapping."
    )
    # The literal 1M markers the helper keys on must be named so the prose
    # can't drift to a different window taxonomy.
    assert "[1m]" in text.lower(), (
        "run.md must name the [1m] suffix marker detect_context_window uses."
    )


def test_run_context_critic_keeps_full_block_at_every_window():
    """The critic's hostile cold read must always get the full block, even
    at 1M — the lean path is never applied to it."""
    text = _read(REPO_ROOT / "skills" / "run" / "references" / "run.md")
    assert "hostile cold read must not be starved" in text, (
        "run.md must carve the critic out of the lean path with its rationale "
        "(the hostile cold read must not be starved)."
    )
    assert "never for the `critic`" in text, (
        "the lean path must explicitly exclude the critic stage."
    )


def test_run_skill_does_not_require_grant():
    """SKILL.md must not require a grant file for autonomous flow."""
    text = _read(REPO_ROOT / "skills" / "run" / "SKILL.md")
    # The v1.2.1 SKILL.md had "v1.2.1+ Autonomous mode procedure" section.
    assert "Autonomous mode procedure" not in text
    # Auto-promote should be cited as the path to hands-off.
    assert "auto-promote" in text.lower() or "auto_promote" in text


# ---------------------------------------------------------------------------
# Pipeline-init skill: uses chat-based gate keyword grammar (v2.2.1 reversal)
# ---------------------------------------------------------------------------
#
# v1.3.0 → v2.1.0 aligned pipeline-init with the modal-gate design that
# the run skill carried. v2.2.1 reverses both back to chat-based gates
# after the operator-UX failure (Cowork's modal overlay hid the chat
# context the operator needed at gate-decision time). The chat surface
# is now the v2.2.1 contract: pipeline-init's scaffold gate, greenfield
# SPEC.md gate, and re-init refresh gate all use chat prompts with
# deterministic first-token keyword parsing.


def test_pipeline_init_skill_uses_chat_gates():
    """v2.2.1: SKILL.md of /pipeline-init must instruct chat-based gates
    with the explicit keyword grammar for the approve/wait/cancel
    decision, NOT modal AskUserQuestion.
    (Formerly test_pipeline_init_skill_references_askuserquestion.)"""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "SKILL.md")
    # The chat keyword grammar must be named so operators reading the
    # skill see the recognized words.
    assert "chat gate" in text.lower() or "chat keyword" in text.lower(), (
        "v2.2.1 pipeline-init SKILL.md must instruct chat-based gates."
    )
    for keyword in ("APPROVE", "WAIT", "CANCEL"):
        assert keyword in text, (
            "v2.2.1 pipeline-init SKILL.md must name the recognized "
            f"chat-gate keyword {keyword!r}."
        )


def test_pipeline_init_skill_describes_v2_2_1_chat_gates():
    """v2.2.1: pipeline-init SKILL.md must describe the v2.2.1 chat-gate
    contract — deterministic first-token keyword parsing — rather than
    the v1.3.0 → v2.1.0 modal flow it superseded.
    (Formerly test_pipeline_init_skill_does_not_ban_askuserquestion.)"""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "SKILL.md")
    # First-token parsing must be documented or referenced.
    assert "first non-whitespace token" in text.lower() or \
           "first-token" in text.lower(), (
        "v2.2.1 pipeline-init SKILL.md must document the deterministic "
        "first-token parsing contract for chat gates."
    )
    # v2.2.1 must be cited so the version-reversal context is preserved.
    assert "v2.2.1" in text, (
        "v2.2.1 pipeline-init SKILL.md must cite v2.2.1 as the chat-gate "
        "reversal version."
    )


def test_pipeline_init_procedure_uses_chat_gates():
    """v2.2.1: references/pipeline-init.md must instruct Claude to print
    chat gate prompts (not invoke AskUserQuestion) for the scaffold /
    re-init / greenfield gates. The chat-keyword grammar must appear at
    each of the three gate points.
    (Formerly test_pipeline_init_procedure_uses_modal_gates.)"""
    text = _read(REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-init.md")
    # At least three chat gate prompts (scaffold, greenfield SPEC, re-init).
    # Each gate prompt uses the `=== <name> gate ===` heading format.
    assert text.count("=== ") >= 3, (
        "v2.2.1 pipeline-init.md should print at least three chat gate "
        "prompts (scaffold, greenfield SPEC.md, re-init refresh)."
    )
    # The recognized chat-gate keywords for scaffold gate.
    for keyword in ("APPROVE", "WAIT", "CANCEL"):
        assert keyword in text, (
            "v2.2.1 pipeline-init.md must use the chat-gate keyword "
            f"{keyword!r}."
        )
    # First-token parsing must be documented at least once.
    assert "first non-whitespace token" in text.lower(), (
        "v2.2.1 pipeline-init.md must document deterministic first-token "
        "keyword parsing."
    )
    # The pre-Pass-5 v0.5.x free-text gate variants must still be gone —
    # v2.2.1's chat ceremony uses the structured `Reply with one word`
    # phrasing, not the looser pre-v1.3.0 forms.
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


def test_readme_describes_chat_gate_reply():
    """v2.2.1: README must describe the chat-gate reply pattern — the
    operator types `APPROVE` (or `REVISE` / `VIEW`) as the first
    non-whitespace token of their next chat message, case-insensitive.
    v1.3.0 → v2.1.0 banned `Reply APPROVE` instructions because the flow
    was modal-click; v2.2.1 reverses to chat with deterministic
    first-token keyword parsing, so the README now DOES instruct the
    chat reply pattern (but with structured prompts naming the
    recognized keywords, not the loose v0.5.x ceremony).
    (Formerly test_readme_does_not_instruct_reply_approve_to_start.)"""
    text = _read(REPO_ROOT / "README.md")
    # The README must name the chat-gate reply pattern using at least
    # one of the canonical instructional phrasings.
    assert (
        "reply `APPROVE`" in text.lower()
        or "reply with one word" in text.lower()
        or "reply approve" in text.lower()
    ), (
        "v2.2.1 README must instruct the operator to reply at the chat "
        "gate with one of the recognized keywords."
    )


def test_readme_upgrade_instruction_targets_v2():
    """README's migration section must direct upgraders at the current
    tag, not the stale v1.1.0 instruction."""
    text = _read(REPO_ROOT / "README.md")
    assert "git checkout v1.1.0" not in text, (
        "README still tells operators to `git checkout v1.1.0` — stale"
    )
    assert "git checkout v2.2.2" in text


def test_user_manual_upgrade_instruction_targets_v2():
    """USER-MANUAL's upgrade snippet must match the README upgrade
    snippet (same instruction in both surfaces)."""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    assert "git checkout v1.1.0" not in text
    assert "git checkout v2.2.2" in text


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


def test_landing_page_stage_flow_uses_chat_approve():
    """v2.2.1: docs/index.html stage-flow diagram must label the three
    gate annotations with `chat APPROVE` (plus the rest of the keyword
    grammar) — chat is the v2.2.1 gate surface. v1.3.0 → v2.1.0 used
    `modal APPROVE` labels; v2.2.1 reverses to chat.
    (Formerly test_landing_page_stage_flow_uses_modal_not_chat_approve.)"""
    text = _read(REPO_ROOT / "docs" / "index.html")
    assert "chat APPROVE" in text, (
        "v2.2.1 landing page stage-flow must label gates as `chat APPROVE`"
    )


def test_landing_page_three_gates_heading_says_chat():
    """v2.2.1: the <h2> for the three-gates section must describe chat-
    based gates (with the deterministic keyword grammar) — chat is the
    v2.2.1 surface.
    (Formerly test_landing_page_three_gates_heading_says_modal.)"""
    text = _read(REPO_ROOT / "docs" / "index.html")
    # Heading should describe chat-based gates.
    assert "Three human gates" in text, (
        "landing page must keep the three-human-gates heading"
    )
    assert "chat" in text.lower(), (
        "v2.2.1 landing page must describe gates as chat"
    )
    # The keyword grammar must appear on the page so operators see the
    # recognized keywords up front.
    for keyword in ("APPROVE", "REVISE", "REPLAN", "VIEW"):
        assert keyword in text, (
            f"v2.2.1 landing page must name the chat-gate keyword {keyword!r}"
        )


def test_landing_page_describes_gates_as_chat_with_deterministic_parsing():
    """v2.2.1: the landing-page copy must describe gates as chat-based
    with deterministic first-token keyword parsing (the v2.2.1 contract).
    The Pass 11b copy `Gates are chat messages, not modal popups.` was
    the inverse of the v1.3.0 → v2.1.0 truth; v2.2.1 reverses again and
    chat IS the gate surface.
    (Formerly test_landing_page_does_not_claim_gates_are_chat_messages_not_modal.)"""
    text = _read(REPO_ROOT / "docs" / "index.html")
    # The Problem section or the three-gates section must describe chat
    # gates with deterministic parsing.
    assert "first non-whitespace token" in text.lower() or \
           "deterministic" in text.lower(), (
        "v2.2.1 landing page must describe chat gates with deterministic "
        "first-token keyword parsing."
    )


def test_landing_page_first_use_describes_chat_approve():
    """v2.2.1: the First-use section must instruct the operator to reply
    `APPROVE` in chat (the v2.2.1 contract). v1.3.0 → v2.1.0 said
    `click APPROVE in the modal`; v2.2.1 reverses to chat reply.
    (Formerly test_landing_page_first_use_does_not_say_approve_in_chat.)"""
    text = _read(REPO_ROOT / "docs" / "index.html")
    # The First-use section must describe replying APPROVE in chat.
    # Use the case-sensitive match for `APPROVE` as a keyword and the
    # case-insensitive match for `reply`/`chat` so the test is robust to
    # phrasing tweaks.
    assert "<code>APPROVE</code>" in text or "reply `APPROVE`" in text.lower(), (
        "v2.2.1 landing-page first-use section must instruct the operator "
        "to reply with the APPROVE keyword in chat."
    )


def test_landing_page_problem_section_describes_chat_approve():
    """v2.2.1: the Problem section's value pitch must describe gates as
    chat-based with the keyword grammar. v1.3.0 → v2.1.0 said `you
    approve in a modal`; v2.2.1 reverses.
    (Formerly test_landing_page_problem_section_does_not_say_approve_in_chat.)"""
    text = _read(REPO_ROOT / "docs" / "index.html")
    # Match the Problem section's chat keyword instruction by grepping
    # for the `<code>APPROVE</code>` token plus chat-reply phrasing.
    assert "chat keyword reply" in text.lower() or \
           "<code>APPROVE</code>" in text, (
        "v2.2.1 landing-page Problem section must describe the chat "
        "keyword reply gate pattern."
    )


# ---------------------------------------------------------------------------
# v2.2.1 chat-gate documentation invariants (inverts the Pass 11d residue sweep)
# ---------------------------------------------------------------------------
#
# v1.3.0 → v2.1.0 Pass 11d swept operator-facing docs for chat-APPROVE
# instructional residue and pinned its ABSENCE. v2.2.1 reverses the gate
# surface after the operator-UX failure (Cowork's modal overlay hid chat
# context at gate-decision time), so the chat keyword grammar is now the
# DOCUMENTED contract. The parametrized residue test below is inverted:
# it now asserts the chat-gate keyword grammar IS PRESENT in operator-
# facing docs, and the per-file tests below assert their specific
# chat-gate description landed.

import re as _re

# Operator-facing surfaces. These are the docs an operator reads BEFORE
# running anything — under v2.2.1 they MUST describe the chat-gate
# keyword grammar. The list is unchanged from Pass 11d so the inversion
# is mechanical: same files, opposite assertion.
_OPERATOR_FACING_FILES = (
    "README.md",
    "USER-MANUAL.md",
    "ARCHITECTURE.md",
    "docs/index.html",
    "docs/module-release-handbook.md",
    "pipelines/roles/manifest-drafter.md",
    "skills/pipeline-init/references/pipeline-payload/pipelines/roles/manifest-drafter.md",
)


# v2.2.1 chat-gate signature patterns. Each pattern matches a phrasing
# that signals the file describes chat-based gates (not modals). At
# least ONE must appear in every operator-facing file.
_CHAT_GATE_SIGNATURE_PATTERNS = (
    # The explicit chat-keyword instruction.
    _re.compile(r"\bchat[- ]gate\b", _re.IGNORECASE),
    # The keyword grammar reference.
    _re.compile(r"\bAPPROVE\s*/\s*REVISE", _re.IGNORECASE),
    # The deterministic first-token parsing description.
    _re.compile(r"\bfirst\s+non-whitespace\s+token\b", _re.IGNORECASE),
    # The literal "reply APPROVE" chat instruction (v2.2.1 chat
    # ceremony reintroduced with structured keyword grammar).
    _re.compile(r"\breply\s+(?:with\s+)?`?APPROVE`?\b", _re.IGNORECASE),
    # The chat APPROVE label used on the landing page stage-flow.
    _re.compile(r"\bchat\s+APPROVE\b", _re.IGNORECASE),
    # The chat keyword reply pattern named on the landing page.
    _re.compile(r"\bchat\s+keyword\s+reply\b", _re.IGNORECASE),
)


@pytest.mark.parametrize("filename", _OPERATOR_FACING_FILES)
def test_no_chat_approve_instructional_residue(filename: str) -> None:
    """v2.2.1: operator-facing docs MUST instruct the chat-gate pattern
    (or describe it). The Pass 11d test pinned its absence; v2.2.1
    inverts to pin its presence. The test name is kept stable so test-
    selection patterns in CI don't need updating, but the docstring and
    the assertion reflect the v2.2.1 inversion. Pre-v2.2.1 (v1.3.0 →
    v2.1.0), the file was supposed to describe modal gates; v2.2.1
    requires chat gates."""
    text = _read(REPO_ROOT / filename)
    matched_patterns: list[str] = []
    for pattern in _CHAT_GATE_SIGNATURE_PATTERNS:
        if pattern.search(text):
            matched_patterns.append(pattern.pattern)
    assert matched_patterns, (
        f"{filename} does not describe v2.2.1 chat-based gates. At least "
        f"one of the chat-gate signature patterns must appear:\n  "
        + "\n  ".join(p.pattern for p in _CHAT_GATE_SIGNATURE_PATTERNS)
        + "\n\nv2.2.1 reverses the v1.3.0 → v2.1.0 modal-gate experiment "
        "after the operator-UX failure where Cowork's modal overlay hid "
        "chat context at gate-decision time. Operator-facing docs must "
        "describe the chat keyword grammar (APPROVE / REVISE / REPLAN / "
        "BLOCK / VIEW) and deterministic first-token parsing."
    )


def test_readme_tagline_describes_chat_gates():
    """v2.2.1: README tagline must describe chat-based gates with the
    keyword grammar. v1.3.0 → v2.1.0 B1 audit required `AskUserQuestion`
    in the tagline; v2.2.1 reverses to chat gates.
    (Formerly test_readme_tagline_describes_modal.)"""
    text = _read(REPO_ROOT / "README.md")
    # The README must instruct the chat-gate pattern.
    assert "APPROVE" in text and "chat" in text.lower(), (
        "v2.2.1 README tagline should describe chat-based gates with the "
        "APPROVE keyword."
    )
    # Sanity: the keyword grammar is named.
    for keyword in ("APPROVE", "REVISE", "REPLAN"):
        assert keyword in text, (
            f"v2.2.1 README should reference the chat-gate keyword {keyword!r}"
        )


def test_architecture_glossary_gate_describes_chat():
    """v2.2.1: ARCHITECTURE.md Glossary `Gate` entry must describe the
    chat-based gate pattern (not the modal pattern). v1.3.0 → v2.1.0
    required `operator clicks APPROVE / REPLAN / BLOCK` or `AskUserQuestion`;
    v2.2.1 reverses to `operator replies` in chat.
    (Formerly test_architecture_glossary_gate_describes_modal.)"""
    text = _read(REPO_ROOT / "ARCHITECTURE.md")
    # The Glossary must describe the chat reply mechanism.
    assert "operator replies" in text.lower() or "reply" in text.lower(), (
        "v2.2.1 ARCHITECTURE.md Glossary `Gate` entry must describe the "
        "operator's chat reply, not a modal click."
    )
    # The keyword grammar must appear.
    for keyword in ("APPROVE", "REVISE", "REPLAN", "BLOCK"):
        assert keyword in text, (
            f"v2.2.1 ARCHITECTURE.md must reference chat-gate keyword {keyword!r}"
        )
    # The pre-v1.3.0 "operator must type APPROVE" idiom (which Pass 11d
    # banned) is also banned under v2.2.1 — the v2.2.1 structured
    # phrasing is "operator replies one of <keywords>", not the loose
    # "must type APPROVE" wording.
    assert "operator must type APPROVE" not in text


def test_manifest_drafter_role_describes_chat_gate():
    """v2.2.1: manifest-drafter.md (and its mirror) must instruct the
    drafter that the operator replies with one of the chat-gate
    keywords (`APPROVE` / `REVISE` / `VIEW`). v1.3.0 → v2.1.0 required
    the drafter to describe the modal click; v2.2.1 reverses to chat.
    (Formerly test_manifest_drafter_role_describes_modal.)"""
    for path in (
        REPO_ROOT / "pipelines" / "roles" / "manifest-drafter.md",
        REPO_ROOT / "skills" / "pipeline-init" / "references" / "pipeline-payload"
        / "pipelines" / "roles" / "manifest-drafter.md",
    ):
        text = _read(path)
        # The role file must describe the chat-gate reply.
        assert "reply" in text.lower() or "reply with one" in text.lower() or \
               "replies with one" in text.lower(), (
            f"{path.name} must describe the operator replying at the "
            "chat gate (v2.2.1 contract)."
        )
        # The chat-gate keyword grammar must be present.
        for keyword in ("APPROVE", "REVISE"):
            assert keyword in text, (
                f"{path.name} must name the v2.2.1 chat-gate keyword "
                f"{keyword!r}"
            )


def test_architecture_diagrams_describe_chat_gates():
    """v2.2.1: ARCHITECTURE.md Mermaid flowchart and sequence diagram
    must label gates as chat (with keyword grammar). v1.3.0 → v2.1.0
    required them to label gates as modal `AskUserQuestion`; v2.2.1
    reverses to chat.
    (Formerly test_architecture_diagrams_describe_modal_gates.)"""
    text = _read(REPO_ROOT / "ARCHITECTURE.md")
    # The flowchart's manifest-gate node label must reference chat.
    # The sequence diagram's GATE 1/2/3 notes must reference chat
    # prompt + keywords.
    assert "chat keyword reply" in text.lower() or \
           "chat prompt" in text.lower(), (
        "v2.2.1 ARCHITECTURE.md diagrams must describe chat-based gates."
    )
    # The pre-Pass-11d "chat-message APPROVE" idiom stays banned (was a
    # different mistake — the diagram described chat but in a way that
    # contradicted the v1.3.0 modal contract). Under v2.2.1 the contract
    # IS chat, so the diagram describes chat, but using the v2.2.1
    # structured phrasing not the loose pre-Pass-11d wording.
    assert "chat-message APPROVE" not in text


def test_user_manual_glossary_manifest_describes_chat_gate():
    """v2.2.1: USER-MANUAL Glossary `Manifest` entry must describe the
    gate as a chat keyword reply (not the modal click). v1.3.0 → v2.1.0
    banned `gated on chat APPROVE` because gates were modal; v2.2.1
    reverses and the chat reply IS the gate.
    (Formerly test_user_manual_glossary_manifest_uses_modal_language.)"""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    # Manifest glossary entry must describe the chat-gate reply.
    assert "chat keyword reply" in text.lower() or \
           "reply" in text.lower(), (
        "v2.2.1 USER-MANUAL Glossary `Manifest` entry must describe the "
        "operator's chat reply to the gate prompt."
    )


def test_user_manual_migration_section_describes_chat_gates():
    """v2.2.1: USER-MANUAL Migration from v0.5.x must describe v2.2.1
    chat gates (with deterministic first-token keyword parsing — the
    v0.5.x ceremony was looser; v2.2.1 keeps the chat surface but with
    explicit structure).
    (Formerly test_user_manual_migration_section_describes_modal_gates.)"""
    text = _read(REPO_ROOT / "USER-MANUAL.md")
    # The Migration section must describe v2.2.1 chat gates.
    assert "first non-whitespace token" in text.lower() or \
           "first-token" in text.lower(), (
        "v2.2.1 USER-MANUAL Migration must describe deterministic "
        "first-token keyword parsing for the chat gates."
    )
    # The pre-v1.3.0 unstructured-chat-message phrasing must not appear.
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
# Judge reincorporation (2026-05-28): the orchestrator propose-execute loop,
# the gated executor protocol, the ARCHITECTURE truth fix, and the preserved
# conservative floors. These pin the live design so a later edit can't
# silently re-introduce the deleted per-tool-call Handler 3a fiction or
# loosen the floors.
# ---------------------------------------------------------------------------

_RUN_MD = REPO_ROOT / "skills" / "run" / "references" / "run.md"
_EXECUTOR_CANON = REPO_ROOT / "pipelines" / "roles" / "executor.md"
_EXECUTOR_PAYLOAD = (
    REPO_ROOT / "skills" / "pipeline-init" / "references"
    / "pipeline-payload" / "pipelines" / "roles" / "executor.md"
)
_JUDGE_CANON = REPO_ROOT / "pipelines" / "roles" / "judge.md"
_JUDGE_PAYLOAD = (
    REPO_ROOT / "skills" / "pipeline-init" / "references"
    / "pipeline-payload" / "pipelines" / "roles" / "judge.md"
)
_ARCHITECTURE = REPO_ROOT / "ARCHITECTURE.md"
_SELF_CLASS_RULES = REPO_ROOT / "pipelines" / "self-classification-rules.md"
_ACTION_CLASSIFICATION = REPO_ROOT / "pipelines" / "action-classification.yaml"
_ACTION_CLASSIFICATION_PAYLOAD = (
    REPO_ROOT / "skills" / "pipeline-init" / "references"
    / "pipeline-payload" / "pipelines" / "action-classification.yaml"
)


def test_run_md_has_judged_executor_handler():
    """run.md Step 7a runs the propose-execute loop, gated on the
    action-classification file existing, classifying then spawning the
    judge then acting on the verdict."""
    text = _read(_RUN_MD)
    assert "Step 7a" in text
    assert ".pipelines/action-classification.yaml" in text
    assert "propose-execute loop" in text
    assert "scripts/classify_action.py" in text
    assert "Spawn the judge" in text
    # All four verdicts are handled.
    for verdict in ("allow", "block", "revise", "escalate"):
        assert f"`{verdict}`" in text, f"run.md Step 7a does not handle verdict {verdict}"
    # The revise cap is pinned at 3.
    assert "3 revise cycles" in text


def test_run_md_writes_the_three_judge_artifacts():
    text = _read(_RUN_MD)
    assert "judge-log.yaml" in text
    assert "judge-metrics.yaml" in text
    assert "judge-decisions/<action_id>.yaml" in text


def test_run_md_uses_the_one_shot_approval_sidecar():
    text = _read(_RUN_MD)
    assert "judge-approved-next.txt" in text
    assert "JUDGE_REVIEW_REQUIRED" in text


def test_run_md_preserves_destructive_secret_floor():
    """Step 7a states the absolute destructive/secret deny floor precedes
    the judge and is never reopened by an allow or the sidecar."""
    text = _read(_RUN_MD)
    assert "Floor precedence" in text
    assert "never loosened" in text
    # The floor precedes the judge and the sidecar does not exempt it.
    assert "precedes the judge" in text


def test_executor_md_has_gated_stop_and_propose_protocol():
    for path in (_EXECUTOR_CANON, _EXECUTOR_PAYLOAD):
        text = _read(path)
        assert "Stop-and-propose protocol" in text, f"{path} missing the protocol"
        assert ".pipelines/action-classification.yaml" in text
        assert "pending-action.yaml" in text
        assert "JUDGE_REVIEW_REQUIRED" in text
        # The proposal carries the fields the judge parses.
        for field in ("action_id", "executor_justification", "executor_evidence"):
            assert field in text, f"{path} proposal missing {field}"


def test_executor_md_copies_are_byte_identical():
    assert _EXECUTOR_CANON.read_bytes() == _EXECUTOR_PAYLOAD.read_bytes(), (
        "executor.md canonical and payload mirror diverged"
    )


def test_judge_md_isolation_reinforcement_present():
    for path in (_JUDGE_CANON, _JUDGE_PAYLOAD):
        text = _read(path)
        assert "load-bearing" in text, f"{path} missing the isolation reinforcement"
        # The reinforcement is specifically about not sharing even when a
        # large shared context window makes it free.
        assert "shared context window" in text
        assert "technically free" in text


def test_judge_md_copies_are_byte_identical():
    assert _JUDGE_CANON.read_bytes() == _JUDGE_PAYLOAD.read_bytes(), (
        "judge.md canonical and payload mirror diverged"
    )


def test_judge_md_confidence_floor_preserved():
    """The confidence-below-0.7 escalate floor must remain in both copies."""
    for path in (_JUDGE_CANON, _JUDGE_PAYLOAD):
        text = _read(path)
        assert "below 0.7" in text, f"{path} lost the confidence-below-0.7 escalate floor"
        assert "escalate" in text


def test_architecture_section7_truth_fix():
    """ARCHITECTURE §7 describes the live propose-execute realization and no
    longer claims a per-tool-call interceptor."""
    text = _read(_ARCHITECTURE)
    assert "wraps every executor tool call" not in text
    assert "Handler 3a" not in text
    assert "propose-execute loop" in text
    assert "orchestrator's altitude" in text


def test_architecture_isolation_is_load_bearing_even_with_1m():
    text = _read(_ARCHITECTURE)
    assert "load-bearing safety, not an artifact of a context-size limit" in text
    assert "1M-token shared context" in text


def test_production_source_halt_floor_preserved():
    """self-classification-rules.md keeps the always-halt-on-production-source
    floor (read-only assertion; the file is not edited by this run)."""
    text = _read(_SELF_CLASS_RULES)
    assert "Production source code changes ALWAYS halt-and-ask" in text


def test_action_classification_default_is_reversible_write():
    """The conservative default class is preserved (file is reused, not
    edited, by this run)."""
    text = _read(_ACTION_CLASSIFICATION)
    assert "default_class: reversible_write" in text


def test_action_classification_comment_matches_propose_execute_model():
    """The opt-in comment must describe the propose-execute model, not the
    deleted per-tool-call 'Handler 3a / every executor action is classified
    and routed' fiction. Guards both byte-identical copies — the
    ARCHITECTURE guard alone let this straggler slip through."""
    for path in (_ACTION_CLASSIFICATION, _ACTION_CLASSIFICATION_PAYLOAD):
        text = _read(path)
        assert "Handler 3a" not in text, f"{path} still references Handler 3a"
        assert "every executor action is classified and routed" not in text, (
            f"{path} still claims the per-tool-call classification fiction"
        )
        # "propose-execute" may wrap across comment lines, so match the token.
        assert "propose-execute" in text, f"{path} missing propose-execute framing"


def test_action_classification_copies_are_byte_identical():
    assert _ACTION_CLASSIFICATION.read_bytes() == _ACTION_CLASSIFICATION_PAYLOAD.read_bytes(), (
        "action-classification.yaml canonical and payload mirror diverged"
    )
