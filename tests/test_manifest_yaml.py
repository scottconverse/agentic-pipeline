# SPDX-License-Identifier: Apache-2.0
"""Shared manifest reader + cross-reader conformance (audit ENG-004).

The pipeline previously parsed `allowed_paths` / `forbidden_paths` with three
independent hand-rolled state machines. The policy gate (check_allowed_paths)
and the PreToolUse hook (hook_utils._manifest_list) now delegate to the single
`manifest_yaml.parse_manifest_list`; scope_lock_utils.parse_simple_yaml is the
remaining independent reader. This test pins the shared reader's behavior AND
asserts every reader extracts the *same* lists from a corpus of manifests —
the divergence the audit flagged as a silent enforcement gap.
"""
from __future__ import annotations

import pytest

from scripts.manifest_yaml import parse_manifest_list
from scripts.check_allowed_paths import _load_manifest_lists
from scripts.scope_lock_utils import parse_simple_yaml, list_value


# name -> (manifest text, expected allowed_paths, expected forbidden_paths)
CORPUS = {
    "nested_block": (
        "pipeline_run:\n"
        "  allowed_paths:\n"
        "    - src/\n"
        "    - tests/\n"
        "  forbidden_paths:\n"
        "    - secrets/\n",
        ["src/", "tests/"],
        ["secrets/"],
    ),
    "top_level_block": (
        "allowed_paths:\n  - a/\n  - b/\nforbidden_paths:\n  - c/\n",
        ["a/", "b/"],
        ["c/"],
    ),
    "inline_comments": (
        "pipeline_run:\n"
        "  allowed_paths:\n"
        "    - src/   # main code\n"
        "    - lib/\n",
        ["src/", "lib/"],
        [],
    ),
    "quoted_values": (
        "pipeline_run:\n"
        "  allowed_paths:\n"
        '    - "src/app/"\n'
        "    - 'tests/'\n",
        ["src/app/", "tests/"],
        [],
    ),
    "empty_flow_list": (
        "pipeline_run:\n  allowed_paths: []\n  forbidden_paths:\n    - x/\n",
        [],
        ["x/"],
    ),
    "sibling_not_absorbed": (
        "pipeline_run:\n"
        "  allowed_paths:\n"
        "    - a/\n"
        "  required_gates:\n"
        "    - policy_passed\n"
        "    - tests_passed\n",
        ["a/"],
        [],
    ),
    "trailing_whitespace": (
        "pipeline_run:\n  allowed_paths:\n    - a/   \n    - b/\n",
        ["a/", "b/"],
        [],
    ),
    "missing_keys": (
        "pipeline_run:\n  risk: low\n",
        [],
        [],
    ),
}


@pytest.mark.parametrize("name", list(CORPUS))
def test_shared_reader_extracts_expected(name):
    text, allowed, forbidden = CORPUS[name]
    assert parse_manifest_list(text, "allowed_paths") == allowed
    assert parse_manifest_list(text, "forbidden_paths") == forbidden


@pytest.mark.parametrize("name", list(CORPUS))
def test_all_readers_agree(name, tmp_path):
    """The policy gate and scope_lock_utils must extract identically to the
    shared reader — no silent divergence (the core ENG-004 risk)."""
    text, allowed, forbidden = CORPUS[name]
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(text, encoding="utf-8")

    gate_allowed, gate_forbidden = _load_manifest_lists(manifest)
    assert gate_allowed == allowed
    assert gate_forbidden == forbidden

    fields = parse_simple_yaml(manifest)
    assert list_value(fields, "allowed_paths") == allowed
    assert list_value(fields, "forbidden_paths") == forbidden


def test_hook_reader_delegates_to_shared(tmp_path):
    """hook_utils._manifest_list now delegates to the shared reader; prove it
    matches on a representative manifest (the hook is the security-critical
    consumer paired with the policy gate)."""
    from hooks.hook_utils import _manifest_list

    text, allowed, forbidden = CORPUS["nested_block"]
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(text, encoding="utf-8")
    assert _manifest_list(manifest, "allowed_paths") == allowed
    assert _manifest_list(manifest, "forbidden_paths") == forbidden
