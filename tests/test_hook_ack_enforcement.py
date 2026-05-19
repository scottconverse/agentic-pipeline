"""v2.2.0: hook-acknowledgement enforcement tests.

Covers the sidecar-driven policy recheck obligation that closes the
v2.0.x "noted, continuing" failure mode where contract-artifact-touched
warnings were acknowledged conversationally and immediately ignored.

The contract under test:
  * record_pending_recheck_for_write appends to .agent-runs/<id>/
    pending-policy-recheck.txt when a Write/Edit successfully targets a
    contract artifact (manifest.yaml / scope-lock.yaml / directive.yaml)
    inside an active non-drafting run dir.
  * policy_recheck_decision denies Write/Edit/non-recheck Bash while
    the sidecar is non-empty. Read/Grep/Glob get a budget of 3 calls.
  * pop_pending_recheck_on_bash_success pops on a Bash matching one of
    the pending recheck commands; run_all.py pops all.

Also pins the v2.2.0 fix to tool_failure_context, which inherited the
v2.0.x substring-only check (the same class of bug v2.1.0 fixed in
classify_tool_risk).
"""

from __future__ import annotations

from pathlib import Path

from hooks.hook_utils import (
    ActiveRun,
    _MAX_READ_ONLY_BEFORE_RECHECK,
    _PENDING_RECHECK_COUNTER,
    _PENDING_RECHECK_SIDECAR,
    policy_recheck_decision,
    pop_pending_recheck_on_bash_success,
    record_pending_recheck_for_write,
    tool_failure_context,
)


def _make_run(
    run_dir: Path, *, drafting: bool = False, run_id: str | None = None
) -> ActiveRun:
    """Build a synthetic ActiveRun anchored at ``run_dir``."""
    rd = run_dir.resolve()
    rd.mkdir(parents=True, exist_ok=True)
    state = rd / "active-control-state.md"
    state.write_text(
        "active_run: " + ("drafting" if drafting else "true") + "\n",
        encoding="utf-8",
    )
    return ActiveRun(
        run_id=run_id or rd.name,
        run_dir=rd,
        state_path=state,
        fields={"active_run": "drafting" if drafting else "true"},
        directive_bound=False,
        judge_active=False,
        is_drafting=drafting,
    )


# ---------------------------------------------------------------------------
# record_pending_recheck_for_write
# ---------------------------------------------------------------------------


def test_record_pending_recheck_appends_manifest_line(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r1")
    manifest = run.run_dir / "manifest.yaml"
    manifest.write_text("# manifest\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(manifest), "content": "# changed\n"},
    }
    line = record_pending_recheck_for_write(event, [run])
    assert line is not None
    assert "check_manifest_immutable.py" in line
    assert run.run_id in line
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    assert sidecar.exists()
    assert line in sidecar.read_text(encoding="utf-8")


def test_record_pending_recheck_appends_scope_lock_line(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r2")
    scope = run.run_dir / "scope-lock.yaml"
    scope.write_text("# scope\n", encoding="utf-8")
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(scope)},
    }
    line = record_pending_recheck_for_write(event, [run])
    assert line is not None
    assert "check_scope_lock.py" in line


def test_record_pending_recheck_appends_directive_line(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r3")
    directive = run.run_dir / "directive.yaml"
    directive.write_text("# directive\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(directive), "content": "# d\n"},
    }
    line = record_pending_recheck_for_write(event, [run])
    assert line is not None
    assert "check_directive_conformance.py" in line


def test_record_pending_recheck_skips_active_control_state(tmp_path: Path) -> None:
    """active-control-state.md is in _CONTRACT_ARTIFACT_NAMES for the warn
    signal but the orchestrator mutates it during every stage transition.
    It is NOT an immutable contract — no recheck obligation."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r4")
    state = run.run_dir / "active-control-state.md"
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(state)},
    }
    line = record_pending_recheck_for_write(event, [run])
    assert line is None
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    assert not sidecar.exists()


def test_record_pending_recheck_idempotent(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r5")
    manifest = run.run_dir / "manifest.yaml"
    manifest.write_text("x\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(manifest), "content": "y\n"},
    }
    first = record_pending_recheck_for_write(event, [run])
    second = record_pending_recheck_for_write(event, [run])
    assert first == second
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    lines = [ln for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, "duplicate pending lines for same artifact"


def test_record_pending_recheck_skips_drafting_run(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r6", drafting=True)
    manifest = run.run_dir / "manifest.yaml"
    manifest.write_text("x\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(manifest), "content": "y\n"},
    }
    line = record_pending_recheck_for_write(event, [run])
    assert line is None


# ---------------------------------------------------------------------------
# policy_recheck_decision — allow paths
# ---------------------------------------------------------------------------


def test_no_pending_means_allow(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r7")
    event = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt"}}
    assert policy_recheck_decision(event, [run]) is None


def test_no_active_run_allows_write() -> None:
    event = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt"}}
    assert policy_recheck_decision(event, []) is None


def test_drafting_run_with_sidecar_allows_write(tmp_path: Path) -> None:
    """Even if a stale sidecar exists on a drafting run, the deny is suppressed.
    Drafting runs have advisory enforcement (Pass 12 / Cluster K)."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r8", drafting=True)
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r8\n",
        encoding="utf-8",
    )
    event = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt"}}
    assert policy_recheck_decision(event, [run]) is None


def test_ask_user_question_is_not_blocked_by_pending(tmp_path: Path) -> None:
    """AskUserQuestion goes through modal_budget_decision, not here.
    policy_recheck_decision returns None for AskUserQuestion."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r9")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r9\n",
        encoding="utf-8",
    )
    event = {"tool_name": "AskUserQuestion", "tool_input": {"question": "?"}}
    assert policy_recheck_decision(event, [run]) is None


# ---------------------------------------------------------------------------
# policy_recheck_decision — deny paths
# ---------------------------------------------------------------------------


def test_pending_blocks_write_to_non_contract_file(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r10")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r10\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "other.txt"), "content": "x"},
    }
    decision = policy_recheck_decision(event, [run])
    assert decision is not None
    out = decision["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "POLICY_RECHECK_REQUIRED" in reason
    assert "check_manifest_immutable.py" in reason


def test_pending_blocks_edit(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r11")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_scope_lock.py --run r11\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "other.txt")},
    }
    decision = policy_recheck_decision(event, [run])
    assert decision is not None
    assert "POLICY_RECHECK_REQUIRED" in decision["hookSpecificOutput"]["permissionDecisionReason"]


def test_pending_blocks_non_recheck_bash(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r12")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_scope_lock.py --run r12\n",
        encoding="utf-8",
    )
    event = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    decision = policy_recheck_decision(event, [run])
    assert decision is not None
    assert "POLICY_RECHECK_REQUIRED" in decision["hookSpecificOutput"]["permissionDecisionReason"]


def test_pending_allows_matching_recheck_bash(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r13")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r13\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python scripts/policy/check_manifest_immutable.py --check --run r13"
        },
    }
    assert policy_recheck_decision(event, [run]) is None


def test_pending_allows_run_all_bash(tmp_path: Path) -> None:
    """run_all.py is the umbrella runner — allowed to satisfy any pending line."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r14")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_directive_conformance.py --run r14\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "python scripts/policy/run_all.py --run r14"},
    }
    assert policy_recheck_decision(event, [run]) is None


# ---------------------------------------------------------------------------
# pop_pending_recheck_on_bash_success
# ---------------------------------------------------------------------------


def test_pop_on_successful_recheck_bash(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r15")
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    sidecar.write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r15\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python scripts/policy/check_manifest_immutable.py --check --run r15"
        },
        "tool_response": {"exit_code": 0},
    }
    popped = pop_pending_recheck_on_bash_success(event, [run])
    assert popped is not None
    assert "check_manifest_immutable.py" in popped
    assert not sidecar.exists() or sidecar.read_text(encoding="utf-8").strip() == ""


def test_pop_does_not_fire_on_failed_recheck(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r16")
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    sidecar.write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r16\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python scripts/policy/check_manifest_immutable.py --check --run r16"
        },
        "tool_response": {"exit_code": 1},
    }
    assert pop_pending_recheck_on_bash_success(event, [run]) is None
    assert sidecar.read_text(encoding="utf-8").strip() != ""


def test_pop_does_not_fire_on_explicit_failure(tmp_path: Path) -> None:
    """Cowork may report success/failure via the ``success`` key instead of
    ``exit_code``. Both are honored."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r17")
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    sidecar.write_text(
        "python scripts/policy/check_scope_lock.py --run r17\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python scripts/policy/check_scope_lock.py --run r17"
        },
        "tool_response": {"success": False},
    }
    assert pop_pending_recheck_on_bash_success(event, [run]) is None
    assert sidecar.read_text(encoding="utf-8").strip() != ""


def test_run_all_pops_all_pending(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r18")
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    sidecar.write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r18\n"
        "python scripts/policy/check_scope_lock.py --run r18\n"
        "python scripts/policy/check_directive_conformance.py --run r18\n",
        encoding="utf-8",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "python scripts/policy/run_all.py --run r18"},
        "tool_response": {"exit_code": 0},
    }
    popped = pop_pending_recheck_on_bash_success(event, [run])
    assert popped is not None
    assert not sidecar.exists() or sidecar.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# Read-only budget
# ---------------------------------------------------------------------------


def test_read_only_budget_three_then_deny(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r19")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r19\n",
        encoding="utf-8",
    )
    event = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    for i in range(_MAX_READ_ONLY_BEFORE_RECHECK):
        result = policy_recheck_decision(event, [run])
        assert result is None, "call #{i} unexpectedly denied".format(i=i)
    decision = policy_recheck_decision(event, [run])
    assert decision is not None
    reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
    assert "budget" in reason
    assert "POLICY_RECHECK_REQUIRED" in reason


def test_read_only_budget_resets_on_new_pending(tmp_path: Path) -> None:
    """A fresh append (e.g. second contract write) resets the read-only budget."""
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r20")
    sidecar = run.run_dir / _PENDING_RECHECK_SIDECAR
    sidecar.write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r20\n",
        encoding="utf-8",
    )
    read_event = {"tool_name": "Grep", "tool_input": {"pattern": "x"}}
    # Burn the budget
    for _ in range(_MAX_READ_ONLY_BEFORE_RECHECK):
        assert policy_recheck_decision(read_event, [run]) is None
    # Fourth would be denied; instead simulate a fresh pending append.
    manifest = run.run_dir / "manifest.yaml"
    manifest.write_text("x", encoding="utf-8")
    record_pending_recheck_for_write(
        {"tool_name": "Write", "tool_input": {"file_path": str(manifest), "content": "y"}},
        [run],
    )
    # Counter should be cleared; next read allowed.
    assert policy_recheck_decision(read_event, [run]) is None


def test_read_only_counter_file_lives_in_run_dir(tmp_path: Path) -> None:
    run = _make_run(tmp_path / ".agent-runs" / "2026-05-19-r21")
    (run.run_dir / _PENDING_RECHECK_SIDECAR).write_text(
        "python scripts/policy/check_manifest_immutable.py --check --run r21\n",
        encoding="utf-8",
    )
    event = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    policy_recheck_decision(event, [run])
    counter_file = run.run_dir / _PENDING_RECHECK_COUNTER
    assert counter_file.exists()
    assert counter_file.read_text(encoding="utf-8").strip() == "1"


# ---------------------------------------------------------------------------
# tool_failure_context — v2.2.0 path-aware extension
# ---------------------------------------------------------------------------


def test_tool_failure_context_does_not_false_positive_on_edit_content(tmp_path: Path) -> None:
    """v2.2.0 fix: the v2.0.x substring check fired the contract-artifact
    warning on any Edit whose tool_input mentioned a contract name in
    new_string (e.g. an edit to hooks/hook_utils.py writing code that
    references manifest.yaml as a string literal). The v2.1.0 fix
    landed in classify_tool_risk but missed tool_failure_context.

    This test pins the v2.2.0 fix: an Edit on a non-contract file whose
    new_string mentions contract artifact names must NOT trigger the
    'pipeline contract artifact touched' message.
    """
    event = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(tmp_path / "hooks" / "hook_utils.py"),
            "old_string": "old code referencing manifest.yaml",
            "new_string": "new code referencing manifest.yaml and scope-lock.yaml",
        },
        "tool_response": {"exit_code": 0},
    }
    ctx = tool_failure_context(event)
    assert "contract artifact" not in ctx.lower()


def test_tool_failure_context_still_warns_on_real_contract_write(tmp_path: Path) -> None:
    """The flip-side: a Write whose file_path target IS a contract artifact
    (under a run dir) should still surface the warning."""
    # Build the .agent-runs/<id>/ path so the path-aware detector
    # recognizes it as a contract artifact.
    run_dir = tmp_path / ".agent-runs" / "r1"
    run_dir.mkdir(parents=True)
    manifest = run_dir / "manifest.yaml"
    manifest.write_text("# m\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(manifest), "content": "# m2\n"},
        "tool_response": {"exit_code": 0},
    }
    ctx = tool_failure_context(event)
    assert "contract artifact" in ctx.lower()


def test_tool_failure_context_does_not_warn_on_cat_manifest(tmp_path: Path) -> None:
    """Read-class Bash should not trigger the warning."""
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat .agent-runs/r1/manifest.yaml"},
        "tool_response": {"exit_code": 0},
    }
    ctx = tool_failure_context(event)
    assert "contract artifact" not in ctx.lower()
