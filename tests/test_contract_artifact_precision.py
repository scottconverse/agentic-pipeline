# SPDX-License-Identifier: Apache-2.0
"""v2.1.0 contract-artifact hook precision tests.

The v2.0.x hook did a substring search on lowercased command/content
for "manifest.yaml" / "scope-lock.yaml" / "directive.yaml". This
produced false-positives when the agent's OWN source code, test
fixtures, or documentation mentioned those names BY NAME -- not as a
target path but as a reference string. v2.1.0 refactors to be path-
aware: the warn fires only when the actual write target is a contract
artifact filename in a recognizable run-dir location.

Additionally, post-pin manifest mutations (after preflight writes
manifest.sha pinning the SHA) are upgraded to DENY: editing a pinned
manifest breaks the integrity contract that downstream stages depend
on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hooks.hook_utils import classify_tool_risk


# --- False-positive prevention (the bug v2.0.x had) ----------------------

def test_no_false_positive_on_write_to_hook_source_referencing_contract_names():
    """Writing the hook_utils.py file itself with content that references
    'manifest.yaml' / 'scope-lock.yaml' by name is NOT a contract touch.
    This was the v2.0.x bug: the substring match made every framework
    edit appear as a contract-artifact write.
    """
    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "hooks/hook_utils.py",
            "content": (
                "# This file references manifest.yaml and scope-lock.yaml and "
                "directive.yaml in its source code for documentation purposes."
            ),
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" not in reasons, (
        "v2.0.x bug regressed: writing source code that references "
        "contract names by string must not flag a contract touch."
    )


def test_no_false_positive_on_test_file_referencing_contract_names():
    """Writing tests/test_modal_budget.py with assertions that mention
    manifest.yaml is NOT a contract touch.
    """
    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "tests/test_modal_budget.py",
            "content": "assert 'manifest.yaml' in scope_lock_text",
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" not in reasons


def test_no_false_positive_on_writing_pipelines_template():
    """Editing .pipelines/manifest-template.yaml is legitimate framework
    work (updating the scaffold). The filename is NOT a contract artifact
    -- 'manifest-template.yaml' != 'manifest.yaml'.
    """
    event = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": ".pipelines/manifest-template.yaml",
            "old_string": "x",
            "new_string": "y",
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" not in reasons


def test_no_false_positive_on_bash_read_of_manifest():
    """`cat .agent-runs/run/manifest.yaml` is a READ. Reads of contract
    artifacts are legitimate orchestration work and must not flag."""
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat .agent-runs/run-x/manifest.yaml"},
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" not in reasons


def test_no_false_positive_on_grep_of_contract_artifact():
    """`grep -n required_gates .agent-runs/run/manifest.yaml` is a read."""
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "grep -n required_gates .agent-runs/run-x/manifest.yaml"},
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" not in reasons


# --- Positive case still works (regression guard) ------------------------

def test_write_to_manifest_in_run_dir_still_warns():
    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".agent-runs/run-x/manifest.yaml",
            "content": "...",
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" in reasons


def test_bash_redirect_to_manifest_still_warns():
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "echo 'mut' > .agent-runs/run-x/manifest.yaml"},
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" in reasons


def test_write_to_scope_lock_in_run_dir_warns():
    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".agent-runs/run-x/scope-lock.yaml",
            "content": "current_rung: x\n",
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" in reasons


def test_write_to_active_control_state_warns():
    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".agent-runs/run-x/active-control-state.md",
            "content": "active_run: true\n",
        },
    }
    severity, reasons = classify_tool_risk(event, [])
    assert "pipeline contract artifact touched" in reasons


# --- Post-pin DENY upgrade (Fix #2) --------------------------------------

def test_post_pin_manifest_write_is_denied(tmp_path, monkeypatch):
    """Once manifest.sha exists in the run dir, any further write to
    manifest.yaml in that dir is DENY (not warn).
    """
    from hooks.hook_utils import ActiveRun, classify_tool_risk

    run_dir = tmp_path / ".agent-runs" / "post-pin-run"
    run_dir.mkdir(parents=True)
    manifest = run_dir / "manifest.yaml"
    manifest.write_text("pipeline_run:\n  id: post-pin-run\n", encoding="utf-8")
    pin = run_dir / "manifest.sha"
    pin.write_text("sha256=abc123\n", encoding="utf-8")
    active_run = ActiveRun(
        run_id="post-pin-run",
        run_dir=run_dir,
        state_path=run_dir / "active-control-state.md",
        fields={"current_stage": "execute"},
        directive_bound=False,
        judge_active=False,
        is_drafting=False,
    )

    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(manifest),
            "content": "pipeline_run:\n  id: post-pin-run\n  goal: tampered\n",
        },
    }
    severity, reasons = classify_tool_risk(event, [active_run])
    assert severity == "deny", (
        "post-pin manifest write must be DENY; got severity=" + severity + " reasons=" + str(reasons)
    )
    assert any("post-pin" in r.lower() for r in reasons), (
        "deny reason must name the post-pin condition; got " + str(reasons)
    )


def test_pre_pin_manifest_write_is_only_warn(tmp_path):
    """Before manifest.sha exists, manifest edits are warn-not-deny."""
    from hooks.hook_utils import ActiveRun, classify_tool_risk

    run_dir = tmp_path / ".agent-runs" / "pre-pin-run"
    run_dir.mkdir(parents=True)
    manifest = run_dir / "manifest.yaml"
    manifest.write_text("pipeline_run:\n  id: pre-pin-run\n", encoding="utf-8")
    # NO manifest.sha file yet
    active_run = ActiveRun(
        run_id="pre-pin-run",
        run_dir=run_dir,
        state_path=run_dir / "active-control-state.md",
        fields={"current_stage": "intake_drafted"},
        directive_bound=False,
        judge_active=False,
        is_drafting=True,
    )

    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(manifest),
            "content": "pipeline_run:\n  id: pre-pin-run\n",
        },
    }
    severity, reasons = classify_tool_risk(event, [active_run])
    # Drafting + pre-pin = warn at most (the existing intake-bridge model)
    assert severity != "deny" or not any("post-pin" in r.lower() for r in reasons), (
        "pre-pin write must not trigger the post-pin DENY; got " + str(reasons)
    )


def test_post_pin_scope_lock_write_only_warns(tmp_path):
    """The post-pin deny is specific to manifest.yaml. scope-lock.yaml
    edits after the SHA pin still warn (legitimate operator-controlled
    edits like switching scope mid-run are warn-worthy but not deny).
    """
    from hooks.hook_utils import ActiveRun, classify_tool_risk

    run_dir = tmp_path / ".agent-runs" / "run-x"
    run_dir.mkdir(parents=True)
    scope_lock = run_dir / "scope-lock.yaml"
    scope_lock.write_text("current_rung: x\n", encoding="utf-8")
    pin = run_dir / "manifest.sha"
    pin.write_text("sha256=abc\n", encoding="utf-8")
    active_run = ActiveRun(
        run_id="run-x",
        run_dir=run_dir,
        state_path=run_dir / "active-control-state.md",
        fields={"current_stage": "execute"},
        directive_bound=False,
        judge_active=False,
        is_drafting=False,
    )

    event = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(scope_lock),
            "content": "current_rung: x-updated\n",
        },
    }
    severity, reasons = classify_tool_risk(event, [active_run])
    # scope-lock edits during execute are noteworthy (warn) but the
    # specific post-pin manifest-only DENY shouldn't fire
    assert not any("post-pin" in r.lower() for r in reasons), (
        "post-pin DENY must not fire on scope-lock edits; got " + str(reasons)
    )
