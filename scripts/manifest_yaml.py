#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Single shared reader for the pipeline's constrained manifest/scope-lock
YAML subset (audit ENG-004).

The project previously parsed the same `key:` / `- item` subset with three
independent hand-rolled state machines — `check_allowed_paths`,
`scope_lock_utils`, and `hooks/hook_utils` — plus a schema validator. Four
readers for one tiny format is four places to drift, and two of them (the
policy-stage gate and the PreToolUse hook) make the SAME security decision
about `allowed_paths` / `forbidden_paths`. If they interpret a manifest
differently, enforcement diverges silently. This module is the one reader they
delegate to; `tests/test_manifest_yaml.py` is the cross-reader conformance
test that keeps them honest.

Stdlib only: the PreToolUse hook must work without PyYAML, so this cannot
depend on it. `policy_utils.unsupported_yaml_constructs` remains the guard that
rejects the richer YAML features the subset disallows.
"""
from __future__ import annotations

from pathlib import Path


def _strip_inline_comment(line: str) -> str:
    """Drop a `#` comment when the `#` is at column 0 or preceded by
    whitespace, so a `#` inside a value (e.g. a URL fragment) is preserved."""
    idx = line.find("#")
    while idx != -1:
        if idx == 0 or line[idx - 1].isspace():
            return line[:idx].rstrip()
        idx = line.find("#", idx + 1)
    return line


def parse_manifest_list(text: str, key: str) -> list[str]:
    """Return the `- item` values under the first `key:` in the manifest subset.

    Indent-aware: collects list items strictly more indented than the `key:`
    line and terminates at the next sibling (a line at or shallower than the
    key's indent). Handles surrounding quotes, inline comments, and `key: []`.
    A flow value on the key line (`key: [a, b]` or a scalar) yields no items —
    the subset uses block lists, and this matches every prior reader.
    """
    in_key = False
    key_indent = -1
    values: list[str] = []
    for raw in text.splitlines():
        line = _strip_inline_comment(raw.rstrip())
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if not in_key:
            if stripped.startswith(f"{key}:"):
                rest = stripped[len(key) + 1 :].strip()
                if rest:
                    # A value on the key line (`[]`, `[a,b]`, or a scalar) is
                    # not a block list — no items to collect.
                    return []
                in_key = True
                key_indent = indent
            continue
        # Block-list collection mode for `key`.
        if stripped.startswith("- "):
            if indent > key_indent:
                values.append(stripped[2:].strip().strip("\"'"))
                continue
            break  # a dash at or shallower than the key indent left the subtree
        if indent <= key_indent:
            break  # a sibling/parent key ends the list
        # A deeper non-list line (e.g. a stray nested mapping) is skipped, and
        # collection continues — matching the hook reader this consolidates.
    return values


def parse_manifest_list_file(path: Path, key: str) -> list[str]:
    """File convenience wrapper around :func:`parse_manifest_list`. BOM-tolerant."""
    return parse_manifest_list(
        path.read_text(encoding="utf-8-sig", errors="replace"), key
    )
