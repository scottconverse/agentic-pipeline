# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_directive_contract.py).
# Includes the 4 PR #5 amendment tests (bind-after-conformance, downstream
# re-verify, exit-3 on resume mismatch, append into existing run.log).

from pathlib import Path

from scripts import auto_promote, check_directive_conformance, check_plan_against_directive
from scripts.directive_utils import sha256_file


MANIFEST = {
    "pipeline_run": {
        "goal": "Ship directive auto approval safely.",
        "expected_outputs": ["Directive auto approval is documented"],
        "definition_of_done": "Manifest, plan, verifier, and manager assertions pass.",
        "non_goals": ["No platform approval bypass"],
        "rollback_plan": "Remove directive.yaml",
        "allowed_paths": ["scripts/"],
        "forbidden_paths": ["docs/"],
    }
}

SCOPE_LOCK = {
    "canonical_source": "docs/release-plan.md",
    "current_rung": "directive-contract",
    "proof_statement": "Only directive contract work is in scope.",
    "allowed_feature_terms": ["directive"],
    "forbidden_future_rung_terms": ["unrelated"],
    "scope_bullets": ["Implement directive contract."],
    "exit_criteria": ["Directive checks pass."],
}


def write_green_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "verifier-report.md").write_text(
        "**Criteria: 1 total, 1 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**",
        encoding="utf-8",
    )
    (run_dir / "critic-report.md").write_text(
        "**Findings: 0 total, 0 blocker, 0 critical, 0 major, 0 minor**",
        encoding="utf-8",
    )
    (run_dir / "drift-report.md").write_text("**Drift: 0 total, 0 blocker**", encoding="utf-8")
    (run_dir / "policy-report.md").write_text("POLICY: ALL CHECKS PASSED", encoding="utf-8")
    (run_dir / "implementation-report.md").write_text("pytest output: 12 passed, 0 failed", encoding="utf-8")


def _write_yaml(path: Path, data: dict) -> None:
    import yaml

    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_directive(run_dir: Path, *, manifest=None, scope_lock=None) -> None:
    directive = {
        "version": 1,
        "author": {"name": "Scott"},
        "authority": {"type": "design_doc", "reference": "docs/design.md"},
        "preapproved": {
            "manifest": manifest if manifest is not None else MANIFEST,
            "scope_lock": scope_lock if scope_lock is not None else SCOPE_LOCK,
        },
        "acceptance": {
            "plan": [
                {
                    "id": "implementation-section",
                    "type": "section",
                    "artifact": "plan.md",
                    "heading": "Implementation",
                    "min_chars": 20,
                },
                {
                    "id": "tests-mentioned",
                    "type": "regex",
                    "artifact": "plan.md",
                    "pattern": "failing-tests-report\\.md",
                },
            ],
            "manager": [
                {
                    "id": "verifier-covers-outputs",
                    "type": "callable",
                    "name": "verifier_covers_manifest_expected_outputs",
                }
            ],
        },
    }
    _write_yaml(run_dir / "directive.yaml", directive)


def _write_run(tmp_path: Path, run_id: str = "directive-run") -> Path:
    run_dir = tmp_path / ".agent-runs" / run_id
    run_dir.mkdir(parents=True)
    _write_yaml(run_dir / "manifest.yaml", MANIFEST)
    _write_yaml(run_dir / "scope-lock.yaml", SCOPE_LOCK)
    _write_directive(run_dir)
    return run_dir


def test_directive_conformance_auto_approves_and_binds_hash(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id, "--bind"])

    assert check_directive_conformance.main() == 0
    out = capsys.readouterr().out
    assert "AUTO_APPROVE" in out
    run_log = (run_dir / "run.log").read_text(encoding="utf-8")
    assert "directive-bound | COMPLETE | hash=" in run_log
    assert sha256_file(run_dir / "directive.yaml") in run_log


def test_directive_nonconformance_falls_back_with_unified_diff(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    _write_run(tmp_path, run_id)
    changed = dict(MANIFEST)
    changed["pipeline_run"] = dict(MANIFEST["pipeline_run"])
    changed["pipeline_run"]["goal"] = "Diverged goal"
    _write_yaml(tmp_path / ".agent-runs" / run_id / "manifest.yaml", changed)
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id, "--bind"])

    assert check_directive_conformance.main() == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "--- directive.preapproved.manifest" in out
    assert "+++ manifest.yaml" in out


def test_directive_bound_line_written_only_on_successful_conformance(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    changed = dict(MANIFEST)
    changed["pipeline_run"] = dict(MANIFEST["pipeline_run"])
    changed["pipeline_run"]["goal"] = "Diverged goal"
    _write_yaml(run_dir / "manifest.yaml", changed)
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id, "--bind"])

    assert check_directive_conformance.main() == 1
    assert "MISMATCH" in capsys.readouterr().out
    assert not (run_dir / "run.log").exists()


def test_absent_directive_preserves_interactive_behavior(tmp_path, monkeypatch, capsys) -> None:
    run_id = "no-directive"
    run_dir = tmp_path / ".agent-runs" / run_id
    run_dir.mkdir(parents=True)
    _write_yaml(run_dir / "manifest.yaml", MANIFEST)
    _write_yaml(run_dir / "scope-lock.yaml", SCOPE_LOCK)
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id])

    assert check_directive_conformance.main() == 1
    assert "NO_DIRECTIVE" in capsys.readouterr().out


def test_plan_directive_auto_approves_green_plan(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    digest = sha256_file(run_dir / "directive.yaml")
    (run_dir / "run.log").write_text(f"2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash={digest}\n", encoding="utf-8")
    (run_dir / "plan.md").write_text(
        "# Plan\n\n## Implementation\n\nWrite failing-tests-report.md before implementation-report.md and cover the directive path.",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_plan_against_directive, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_against_directive.py", "--run", run_id])

    assert check_plan_against_directive.main() == 0
    assert "AUTO_APPROVE" in capsys.readouterr().out


def test_directive_modified_mid_run_blocks_next_auto_approval(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    digest = sha256_file(run_dir / "directive.yaml")
    (run_dir / "run.log").write_text(f"2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash={digest}\n", encoding="utf-8")
    text = (run_dir / "directive.yaml").read_text(encoding="utf-8")
    (run_dir / "directive.yaml").write_text(text + "\n# tamper\n", encoding="utf-8")
    (run_dir / "plan.md").write_text("## Implementation\n\nfailing-tests-report.md", encoding="utf-8")
    monkeypatch.setattr(check_plan_against_directive, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_against_directive.py", "--run", run_id])

    assert check_plan_against_directive.main() == 2
    assert "hash changed" in capsys.readouterr().out


def test_auto_promote_requires_directive_manager_assertions(tmp_path, monkeypatch) -> None:
    run_id = "green-run"
    run_base = tmp_path / ".agent-runs"
    run_dir = run_base / run_id
    write_green_run(run_dir)
    _write_yaml(run_dir / "manifest.yaml", MANIFEST)
    _write_yaml(run_dir / "scope-lock.yaml", SCOPE_LOCK)
    _write_directive(run_dir)
    digest = sha256_file(run_dir / "directive.yaml")
    (run_dir / "run.log").write_text(f"2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash={digest}\n", encoding="utf-8")
    (run_dir / "verifier-report.md").write_text(
        "**Criteria: 1 total, 1 MET, 0 PARTIAL, 0 NOT MET, 0 NOT APPLICABLE**\n"
        "Directive auto approval is documented",
        encoding="utf-8",
    )
    monkeypatch.setattr(auto_promote, "RUN_DIR_BASE", run_base)
    monkeypatch.setattr(auto_promote, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["auto_promote.py", "--run", run_id])

    assert auto_promote.main() == 0
    decision = (run_dir / "manager-decision.md").read_text(encoding="utf-8")
    assert "Directive hash" in decision
    assert "directive-manager:verifier-covers-outputs" in decision


def test_auto_promote_blocks_when_directive_bound_hash_mismatches_on_resume(tmp_path, monkeypatch) -> None:
    run_id = "green-run"
    run_base = tmp_path / ".agent-runs"
    run_dir = run_base / run_id
    write_green_run(run_dir)
    _write_yaml(run_dir / "manifest.yaml", MANIFEST)
    _write_yaml(run_dir / "scope-lock.yaml", SCOPE_LOCK)
    _write_directive(run_dir)
    (run_dir / "run.log").write_text("2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash=" + "0" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(auto_promote, "RUN_DIR_BASE", run_base)
    monkeypatch.setattr(auto_promote, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["auto_promote.py", "--run", run_id])

    assert auto_promote.main() == 1
    report = (run_dir / "auto-promote-report.md").read_text(encoding="utf-8")
    assert "directive-manager:integrity" in report


def test_downstream_auto_promote_re_verifies_manifest_conformance(tmp_path, monkeypatch) -> None:
    run_id = "green-run"
    run_base = tmp_path / ".agent-runs"
    run_dir = run_base / run_id
    write_green_run(run_dir)
    _write_yaml(run_dir / "manifest.yaml", MANIFEST)
    _write_yaml(run_dir / "scope-lock.yaml", SCOPE_LOCK)
    _write_directive(run_dir)
    digest = sha256_file(run_dir / "directive.yaml")
    (run_dir / "run.log").write_text(f"2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash={digest}\n", encoding="utf-8")
    changed = dict(MANIFEST)
    changed["pipeline_run"] = dict(MANIFEST["pipeline_run"])
    changed["pipeline_run"]["goal"] = "Diverged goal"
    _write_yaml(run_dir / "manifest.yaml", changed)
    monkeypatch.setattr(auto_promote, "RUN_DIR_BASE", run_base)
    monkeypatch.setattr(auto_promote, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["auto_promote.py", "--run", run_id])

    assert auto_promote.main() == 1
    report = (run_dir / "auto-promote-report.md").read_text(encoding="utf-8")
    assert "directive-manifest-conformance" in report


def test_resume_with_bound_directive_and_diverged_manifest_returns_exit_3(tmp_path, monkeypatch, capsys) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    digest = sha256_file(run_dir / "directive.yaml")
    (run_dir / "run.log").write_text(f"2026-05-16T00:00:00Z | directive-bound | COMPLETE | hash={digest}\n", encoding="utf-8")
    changed = dict(MANIFEST)
    changed["pipeline_run"] = dict(MANIFEST["pipeline_run"])
    changed["pipeline_run"]["goal"] = "Diverged goal"
    _write_yaml(run_dir / "manifest.yaml", changed)
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id, "--bind"])

    assert check_directive_conformance.main() == 3
    out = capsys.readouterr().out
    assert "CONTRACT_DIVERGED" in out
    assert "explicit operator acknowledgment" in out


def test_bind_into_existing_run_log_appends_and_preserves_prior_content(tmp_path, monkeypatch) -> None:
    run_id = "directive-run"
    run_dir = _write_run(tmp_path, run_id)
    prior = "2026-05-16T00:00:00Z | preflight | COMPLETE | prior event\n"
    (run_dir / "run.log").write_text(prior, encoding="utf-8")
    monkeypatch.setattr(check_directive_conformance, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["check_directive_conformance.py", "--run", run_id, "--bind"])

    assert check_directive_conformance.main() == 0
    lines = (run_dir / "run.log").read_text(encoding="utf-8").splitlines()
    assert lines[0] == prior.strip()
    assert "directive-bound | COMPLETE | hash=" in lines[1]
