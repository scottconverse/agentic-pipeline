"""Tests for scripts/check_stage_done.py — v1.2.0 STAGE_DONE marker enforcement."""

from __future__ import annotations

from pathlib import Path

import sys
import yaml
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_stage_done as csd  # type: ignore  # noqa: E402


def _setup_run(
    tmp_path: Path,
    pipeline_type: str = "feature",
    stages: list[dict] | None = None,
    log_content: str = "",
) -> Path:
    repo = tmp_path
    pipelines = repo / ".pipelines"
    pipelines.mkdir(parents=True)
    if stages is None:
        stages = [
            {"name": "manifest", "role": "human"},
            {"name": "research", "role": "researcher"},
            {"name": "plan", "role": "planner"},
            {"name": "execute", "role": "executor"},
            {"name": "policy", "role": "pipeline"},
        ]
    (pipelines / f"{pipeline_type}.yaml").write_text(
        yaml.safe_dump({"pipeline": pipeline_type, "stages": stages}),
        encoding="utf-8",
    )
    run_dir = repo / ".agent-runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"pipeline_run": {"id": "test-run", "type": pipeline_type}}),
        encoding="utf-8",
    )
    (run_dir / "run.log").write_text(log_content, encoding="utf-8")
    return repo


def test_all_markers_present_passes(tmp_path: Path) -> None:
    repo = _setup_run(
        tmp_path,
        log_content=(
            "STAGE_DONE: manifest\n"
            "STAGE_DONE: research\n"
            "STAGE_DONE: plan\n"
            "STAGE_DONE: execute\n"
        ),
    )
    missing, found, _ = csd.evaluate("test-run", repo)
    assert missing == []
    assert "execute" in found


def test_missing_marker_is_flagged(tmp_path: Path) -> None:
    repo = _setup_run(
        tmp_path,
        log_content=(
            "STAGE_DONE: manifest\n"
            "STAGE_DONE: research\n"
            # plan and execute missing
        ),
    )
    missing, found, _ = csd.evaluate("test-run", repo)
    assert "plan" in missing
    assert "execute" in missing


def test_through_truncates_expected(tmp_path: Path) -> None:
    """--through limits the check to stages up to and including the named stage."""
    repo = _setup_run(
        tmp_path,
        log_content="STAGE_DONE: manifest\nSTAGE_DONE: research\n",
    )
    missing, found, _ = csd.evaluate("test-run", repo, through="research")
    assert missing == []  # only required up to research; later stages not required yet


def test_pipeline_owned_stages_skipped(tmp_path: Path) -> None:
    """policy / auto-promote stages are owned by orchestrator, no STAGE_DONE required."""
    repo = _setup_run(
        tmp_path,
        stages=[
            {"name": "manifest", "role": "human"},
            {"name": "execute", "role": "executor"},
            {"name": "policy", "role": "pipeline"},  # pipeline-owned, no marker
        ],
        log_content="STAGE_DONE: manifest\nSTAGE_DONE: execute\n",
    )
    missing, found, _ = csd.evaluate("test-run", repo)
    assert missing == []
    assert "policy" not in missing  # was never expected


# v3.0.0 (Opus 4.8 retarget): tolerant STAGE_DONE marker. A more verbose
# model may append context, drop the colon, or vary case/separator. Every
# legacy `STAGE_DONE: <stage>` line still matches; these add the tolerant
# variants. Over-matching only adds spurious names to `found` (ignored) and
# can never drop a required stage, so it cannot cause a false PASS.


def test_tolerant_markers_with_trailing_text_no_colon_and_case(tmp_path: Path) -> None:
    repo = _setup_run(
        tmp_path,
        log_content=(
            "STAGE_DONE: manifest -- manifest.yaml drafted (6045 bytes)\n"  # trailing text
            "STAGE_DONE: research  research.md written\n"                    # trailing text
            "STAGE_DONE execute\n"                                          # no colon
            "stage-done: plan\n"                                            # case + separator
        ),
    )
    missing, found, _ = csd.evaluate("test-run", repo)
    assert missing == []
    assert {"manifest", "research", "plan", "execute"} <= set(found)


def test_unrelated_prose_does_not_satisfy_a_required_stage(tmp_path: Path) -> None:
    """A line that merely starts 'stage done ...' with a non-stage token must
    not satisfy a required stage — guards the tolerant pattern from false PASS."""
    repo = _setup_run(
        tmp_path,
        stages=[
            {"name": "manifest", "role": "human"},
            {"name": "execute", "role": "executor"},
        ],
        log_content="STAGE_DONE: manifest\nstage done by the team for the day\n",
    )
    missing, found, _ = csd.evaluate("test-run", repo)
    assert "execute" in missing  # never satisfied by the unrelated prose line
