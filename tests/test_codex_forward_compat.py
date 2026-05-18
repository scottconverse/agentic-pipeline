# SPDX-License-Identifier: Apache-2.0
"""Codex forward-compatibility audit per PRD section 9 item 13.

The PRD requires that records written by agent-pipeline-claude (Layer A
file-backed) and adopted by Mem0 (Layer B) use the same schema codex
uses for its v0.9 file-backed memory. This file asserts that schema
match programmatically against a checked-in snapshot of the codex
v0.9.0 surface, so future drift in either repo surfaces as a test
failure.

Snapshots are committed under tests/fixtures/codex_v0_9_0/. To refresh:
copy the named files from a fresh clone of agent-pipeline-codex tagged
v0.9.0 into the fixture dir and run pytest -q.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_HOOK_UTILS = REPO_ROOT / "hooks" / "hook_utils.py"
CLAUDE_DIRECTIVE_UTILS = REPO_ROOT / "scripts" / "directive_utils.py"
CLAUDE_AUTO_PROMOTE = REPO_ROOT / "scripts" / "auto_promote.py"
CODEX_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "codex_v0_9_0"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _get_constant(source: str, name: str):
    """Extract a module-level constant by name from a Python source file."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in source")


def _get_function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise KeyError(f"function {name} not found in source")


# ---------------------------------------------------------------------------
# Layer A memory record schema must match codex verbatim
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not CODEX_FIXTURE.exists(), reason="codex v0.9.0 fixture not vendored; refresh tests/fixtures/codex_v0_9_0/")
def test_memory_file_routing_matches_codex() -> None:
    """Codex's _memory_file_for_event maps event names to the right *.jsonl.
    Claude extended the routing for PostToolUseFailure/PreCompact/PostCompact/
    SubagentStop/SessionEnd but must not have changed routing for the codex 6.
    """
    claude_source = _read(CLAUDE_HOOK_UTILS)
    codex_source = _read(CODEX_FIXTURE / "hook_utils.py")

    # Build a routing table for each by exercising the function
    claude_routing = {}
    codex_routing = {}
    for event in ("UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop", "SessionStart"):
        claude_routing[event] = _route_for_event(claude_source, event)
        codex_routing[event] = _route_for_event(codex_source, event)

    assert claude_routing == codex_routing, (
        f"memory file routing diverged from codex v0.9.0:\n"
        f"  claude: {claude_routing}\n"
        f"  codex:  {codex_routing}"
    )


def _route_for_event(source: str, event: str) -> str:
    """Simulate _memory_file_for_event(event) by parsing the function body."""
    namespace: dict = {}
    exec(_get_function_source(source, "_memory_file_for_event"), namespace)
    return namespace["_memory_file_for_event"](event)


@pytest.mark.skipif(not CODEX_FIXTURE.exists(), reason="codex v0.9.0 fixture not vendored")
def test_directive_bound_regex_matches_codex() -> None:
    """The directive-bound run.log line shape must be readable cross-pipeline."""
    claude_pattern = _extract_re_compile_arg(_read(CLAUDE_DIRECTIVE_UTILS), "DIRECTIVE_BOUND_RE")
    codex_pattern = _extract_re_compile_arg(_read(CODEX_FIXTURE / "directive_utils.py"), "DIRECTIVE_BOUND_RE")

    assert claude_pattern == codex_pattern, (
        f"DIRECTIVE_BOUND_RE pattern diverged from codex; "
        f"cross-pipeline run.log reads would mis-parse the binding line.\n"
        f"claude: {claude_pattern!r}\ncodex:  {codex_pattern!r}"
    )


def _extract_re_compile_arg(source: str, name: str) -> str:
    """Find `NAME = re.compile(<string-literal>, ...)` and return the literal."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                if isinstance(node.value, ast.Call):
                    first_arg = node.value.args[0]
                    return ast.literal_eval(first_arg)
    raise KeyError(f"{name} = re.compile(...) not found in source")


@pytest.mark.skipif(not CODEX_FIXTURE.exists(), reason="codex v0.9.0 fixture not vendored")
def test_max_memory_text_constant_matches_codex() -> None:
    claude_max = _get_constant(_read(CLAUDE_HOOK_UTILS), "MAX_MEMORY_TEXT")
    codex_max = _get_constant(_read(CODEX_FIXTURE / "hook_utils.py"), "MAX_MEMORY_TEXT")

    assert claude_max == codex_max, (
        f"MAX_MEMORY_TEXT diverged: claude={claude_max} codex={codex_max}. "
        "Cross-pipeline truncation must agree."
    )


@pytest.mark.skipif(not CODEX_FIXTURE.exists(), reason="codex v0.9.0 fixture not vendored")
def test_max_handoff_records_matches_codex() -> None:
    claude_max = _get_constant(_read(CLAUDE_HOOK_UTILS), "MAX_HANDOFF_RECORDS")
    codex_max = _get_constant(_read(CODEX_FIXTURE / "hook_utils.py"), "MAX_HANDOFF_RECORDS")

    assert claude_max == codex_max


# ---------------------------------------------------------------------------
# Directive contract data shape must be readable by codex
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not CODEX_FIXTURE.exists(), reason="codex v0.9.0 fixture not vendored")
def test_directive_required_fields_match_codex() -> None:
    """Both impls reject a directive missing any of: version, author, authority,
    preapproved, acceptance. A claude-written directive must parse on codex."""
    claude_source = _read(CLAUDE_DIRECTIVE_UTILS)
    codex_source = _read(CODEX_FIXTURE / "directive_utils.py")

    claude_fields = _extract_required_fields(claude_source)
    codex_fields = _extract_required_fields(codex_source)

    assert claude_fields == codex_fields, (
        f"directive required fields diverged: claude={claude_fields} codex={codex_fields}"
    )


def _extract_required_fields(source: str) -> set[str]:
    """Find the tuple literal in `for field in (...)` inside load_directive."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_directive":
            for child in ast.walk(node):
                if isinstance(child, ast.For) and isinstance(child.iter, ast.Tuple):
                    return {ast.literal_eval(e) for e in child.iter.elts}
    return set()


# ---------------------------------------------------------------------------
# PRD metadata taxonomy is documented in the schema; codex must accept the
# same vocabulary if it adds Mem0 support in v0.10+
# ---------------------------------------------------------------------------


def test_mem0_config_schema_declares_full_type_taxonomy() -> None:
    schema_path = REPO_ROOT / "schemas" / "mem0.config.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    allowed = schema["properties"]["writes"]["properties"]["allowed_types"]["items"]["enum"]
    expected = {
        "decision", "task_learning", "anti_pattern",
        "user_preference", "environmental", "convention", "session_state",
    }
    assert set(allowed) == expected, (
        f"mem0.config.v1.json allowed_types diverged from PRD FR-7 taxonomy. "
        f"got: {allowed}; expected: {sorted(expected)}"
    )


def test_directive_template_callable_names_are_public_locals() -> None:
    """Per directive_utils._assert_callable, registered callable names must
    be local public names (no dot, no underscore prefix). The shipped
    template uses callables in two sections:
      - acceptance.plan -> resolved against check_plan_against_directive.py
      - acceptance.manager -> resolved against auto_promote.py
    """
    import re
    template_path = REPO_ROOT / "pipelines" / "directive-template.yaml"
    text = template_path.read_text(encoding="utf-8")
    plan_source = _read(REPO_ROOT / "scripts" / "check_plan_against_directive.py")
    manager_source = _read(CLAUDE_AUTO_PROMOTE)

    # Find the plan: section and the manager: section, extract callable names from each.
    plan_block, manager_block = _split_acceptance_sections(text)

    plan_names = re.findall(r'name:\s*"([a-z_]+)"', plan_block)
    manager_names = re.findall(r'name:\s*"([a-z_]+)"', manager_block)

    assert plan_names, "no plan callable names found in directive-template.yaml"
    assert manager_names, "no manager callable names found in directive-template.yaml"

    for name in plan_names:
        assert "." not in name and not name.startswith("_"), name
        assert f"def {name}(" in plan_source, (
            f"acceptance.plan references callable {name!r} but check_plan_against_directive.py "
            f"does not define it (callable_namespace=__name__ for that file)."
        )
    for name in manager_names:
        assert "." not in name and not name.startswith("_"), name
        assert f"def {name}(" in manager_source, (
            f"acceptance.manager references callable {name!r} but auto_promote.py "
            f"does not define it (callable_namespace=__name__ for that file)."
        )


def _split_acceptance_sections(template_text: str) -> tuple[str, str]:
    """Best-effort YAML slice: returns (plan_block, manager_block) by line index."""
    lines = template_text.splitlines()
    plan_start = next(i for i, l in enumerate(lines) if l.strip() == "plan:")
    manager_start = next(i for i, l in enumerate(lines) if l.strip() == "manager:")
    return "\n".join(lines[plan_start:manager_start]), "\n".join(lines[manager_start:])
