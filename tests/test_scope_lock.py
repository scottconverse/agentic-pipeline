# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_scope_lock.py).

from pathlib import Path

from scripts.check_release_docs_consistency import evaluate_release_docs_consistency
from scripts.check_rung_file_ownership import evaluate_rung_file_ownership
from scripts.check_scope_lock import evaluate_scope_lock


def _write_plan(root: Path) -> None:
    plan = root / "docs" / "spec" / "release-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        """
# Release plan

## 0.6 - Summary + signed records

Proves: an AI summary of a meeting passes operator review and exports as a signed PDF/A legal record

Scope:
- civiccast-summary creates sourced claims with transcript timestamp citations.
- civiccast-records exports signed transcript records as PDF/A.

Exit criteria:
- Operator review accepts the summary.
- Signed PDF/A legal record exists.

## 0.7 - Publish dashboard

Proves: reviewed records can be sent through a three-tier publish dashboard.

Scope:
- publish dashboard
- internet archive
- local NAS
- YouTube syndication
""",
        encoding="utf-8",
    )


def _write_scope_lock(root: Path, run_id: str = "run-1") -> Path:
    run = root / ".agent-runs" / run_id
    run.mkdir(parents=True)
    path = run / "scope-lock.yaml"
    path.write_text(
        """
current_rung: "0.6"
canonical_source: "docs/spec/release-plan.md"
rung_title: "Summary + signed records"
proves: "an AI summary of a meeting passes operator review and exports as a signed PDF/A legal record"
required_modules:
  - civiccast-summary
  - civiccast-records
allowed_feature_terms:
  - summary
  - sourced claim
  - transcript timestamp
  - PDF/A
  - signed transcript
  - records
forbidden_feature_terms_without_replan:
  - publish dashboard
  - internet archive
  - local NAS
  - YouTube
  - syndication
  - three-tier publish
scope_bullets:
  - civiccast-summary creates sourced claims with transcript timestamp citations.
exit_criteria:
  - Signed PDF/A legal record exists.
""",
        encoding="utf-8",
    )
    return path


def _fixture(root: Path) -> None:
    _write_plan(root)
    _write_scope_lock(root)


def test_scope_lock_passes_when_it_matches_release_plan(tmp_path: Path) -> None:
    _fixture(tmp_path)

    assert evaluate_scope_lock("run-1", run_dir=tmp_path / ".agent-runs", root=tmp_path) == []


def test_scope_lock_missing_fails_before_product_work(tmp_path: Path) -> None:
    _write_plan(tmp_path)

    violations = evaluate_scope_lock("run-1", run_dir=tmp_path / ".agent-runs", root=tmp_path)

    assert any("scope-lock.yaml missing" in item for item in violations)


def test_scope_lock_title_mismatch_is_scope_conflict(tmp_path: Path) -> None:
    _fixture(tmp_path)
    lock = tmp_path / ".agent-runs" / "run-1" / "scope-lock.yaml"
    lock.write_text(lock.read_text(encoding="utf-8").replace("Summary + signed records", "Publish dashboard"), encoding="utf-8")

    violations = evaluate_scope_lock("run-1", run_dir=tmp_path / ".agent-runs", root=tmp_path)

    assert any("SCOPE_CONFLICT" in item and "release-plan.md says v0.6" in item for item in violations)


def test_rung_file_ownership_blocks_future_rung_paths_and_commit_message(tmp_path: Path) -> None:
    _fixture(tmp_path)

    violations = evaluate_rung_file_ownership(
        "run-1",
        run_dir=tmp_path / ".agent-runs",
        root=tmp_path,
        commit_message="feat: add publish dashboard foundation",
        paths=[
            "civiccast/publish/PublishDashboardScreen.tsx",
            "docs/releases/evidence/v0.6-publish-dashboard-smoke.md",
        ],
    )

    assert any("commit message contains `publish dashboard`" in item for item in violations)
    assert any("PublishDashboardScreen" in item or "publish dashboard" in item for item in violations)


def test_release_docs_consistency_blocks_v06_publish_dashboard_claim(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "README.md").write_text("Current work: v0.6 publish dashboard in progress\n", encoding="utf-8")

    violations = evaluate_release_docs_consistency(
        "run-1",
        run_dir=tmp_path / ".agent-runs",
        root=tmp_path,
    )

    assert any("README.md:1" in item and "SCOPE_CONFLICT" in item for item in violations)
