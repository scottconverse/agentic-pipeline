#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Map an executor tool call to one of four risk classes.

The judge layer (ARCHITECTURE section 7) routes each external-facing or
high-risk action to a context-isolated judge subagent before it executes.
This helper is the pure-Python classifier the orchestrator and the
PreToolUse hook consult: given a tool name and a command string it reads
pipelines/action-classification.yaml and returns the risk class.

Risk classes, in most-restrictive-first evaluation order:

    high_risk         irreversible / externally visible / shared state
    external_facing   leaves the local machine
    reversible_write  local writes recoverable via git or undo
    read_only         observation only, no state change

Actions in the file's `human_only_under_autonomous` class are reported as
`high_risk`, matching the file's own statement that the judge layer treats
them as high_risk regardless of grant. Unmatched actions fall back to the
file's `default_class` (reversible_write).

A command that matches more than one class is assigned the most
restrictive class *that has a matching rule*, so the judge sees the worst
case among recognized patterns (e.g. `cat x && rm -rf y` -> high_risk). The
rule set is a denylist and cannot be exhaustive; the PreToolUse hook's
DESTRUCTIVE_PATTERNS (hardened in v3.0.1, audit ENG-001) remains the deny floor.

The classification file lives at `<repo>/pipelines/action-classification.yaml`
relative to this script. Because this module is mirrored byte-for-byte into
the pipeline-init payload (whose scripts/ and pipelines/ siblings have the
same layout), the parent.parent path resolves correctly in both the
canonical and the installed-payload locations.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


DEFAULT_CLASSIFICATION_PATH = (
    Path(__file__).resolve().parent.parent / "pipelines" / "action-classification.yaml"
)

# Classes in most-restrictive-first order. The first class with a matching
# rule wins. human_only_under_autonomous is evaluated first and reported as
# high_risk.
_CLASS_EVAL_ORDER = (
    "human_only_under_autonomous",
    "high_risk",
    "external_facing",
    "reversible_write",
    "read_only",
)

# Classes that must stop-and-propose to the judge before executing.
_JUDGE_REQUIRED_CLASSES = frozenset({"external_facing", "high_risk"})

# Map a canonical tool name (as written in action-classification.yaml,
# codex-style lowercase) to the set of actual tool names that match it, so
# the same policy file classifies both codex-style and Claude-style tools.
_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "bash": frozenset({"bash"}),
    "str_replace_editor": frozenset(
        {"str_replace_editor", "edit", "multiedit", "notebookedit"}
    ),
    "create_file": frozenset({"create_file", "write"}),
}


def load_classification(path: Path | str | None = None) -> dict:
    """Load and return the parsed action-classification.yaml mapping."""
    target = Path(path) if path is not None else DEFAULT_CLASSIFICATION_PATH
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def requires_judge(risk_class: str) -> bool:
    """True if an action of this class must be routed to the judge."""
    return risk_class in _JUDGE_REQUIRED_CLASSES


def _tool_matches(rule_tool: str | None, actual_tool: str) -> bool:
    """A rule with no `tool` matches any tool. Otherwise the actual tool must
    be in the rule tool's alias set (case-insensitive)."""
    if not rule_tool:
        return True
    actual = (actual_tool or "").strip().lower()
    canonical = rule_tool.strip().lower()
    aliases = _TOOL_ALIASES.get(canonical)
    if aliases is None:
        return actual == canonical
    return actual in aliases


def _rule_matches(rule: dict, tool_name: str, command: str) -> bool:
    if not _tool_matches(rule.get("tool"), tool_name):
        return False
    pattern = rule.get("pattern")
    if not pattern:
        return True
    return re.search(pattern, command or "", re.IGNORECASE) is not None


def classify_action(
    tool_name: str,
    command: str,
    config: dict | None = None,
    *,
    config_path: Path | str | None = None,
) -> str:
    """Return the risk class for a (tool_name, command) action.

    Evaluates the classes most-restrictive-first; the first class with a
    matching rule wins. human_only_under_autonomous is reported as
    high_risk. Unmatched actions return the file's default_class.
    """
    data = config if config is not None else load_classification(config_path)
    classification = data.get("classification") or {}
    for class_name in _CLASS_EVAL_ORDER:
        for rule in classification.get(class_name) or []:
            if _rule_matches(rule, tool_name, command):
                if class_name == "human_only_under_autonomous":
                    return "high_risk"
                return class_name
    return data.get("default_class", "reversible_write")


def main(argv: list[str] | None = None) -> int:
    """CLI: print the risk class for a (tool, command) action.

    Usage: ``python scripts/classify_action.py <tool> "<command>"``. The
    orchestrator's judged-executor loop (run.md Step 7a) calls this to classify
    a pending action before routing it to the judge. Prints exactly the class
    name (one of read_only / reversible_write / external_facing / high_risk).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Classify an executor (tool, command) action into a risk class."
    )
    parser.add_argument("tool", help="Tool name, e.g. bash")
    parser.add_argument("command", help="The command or arguments string")
    parser.add_argument(
        "--config-path",
        default=None,
        help="Path to action-classification.yaml (default: the sibling pipelines/ copy).",
    )
    args = parser.parse_args(argv)
    print(classify_action(args.tool, args.command, config_path=args.config_path))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
