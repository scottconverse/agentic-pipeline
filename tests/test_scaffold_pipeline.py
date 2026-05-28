"""Deterministic $0 verification of the pipeline-init scaffold.

This is the unit-tier sibling of `test_cleanroom_smoke.py`. The smoke
test exercises the full LLM-driven path (model invokes Skill, claude
CLI executes tool calls, scaffold materializes) at ~$0.05/run and
~60s wall. This test exercises just the deterministic copy step at
$0 and sub-second wall.

Both tests assert the same load-bearing post-conditions: a `.pipelines/`
tree with the expected role files + pipeline yamls + a `scripts/policy/`
tree with the expected validators. If the payload drifts, BOTH tests
fail — but this one fails first, fast, in CI, without an API key.

Coverage gap vs cleanroom-smoke: this test does NOT prove that the LLM
correctly invokes the skill, that claude CLI's Skill tool dispatch
works, or that the markdown step 3 instructions are followed. Those
remain covered by `test_skill_packaging.py`, the `claude plugin list`
load check, and manual Cowork-app verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.scaffold_pipeline import (
    DEFAULT_PAYLOAD,
    ScaffoldError,
    scaffold,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PIPELINES_DIR = _REPO_ROOT / "pipelines"
_RUN_MD = _REPO_ROOT / "skills" / "run" / "references" / "run.md"

# v3.0.0 (WS-1) per-stage execution hints.
_VALID_EFFORT = {"high", "extra", "max"}
_VALID_SPEED = {"fast"}

# Stage character → expected hint, keyed by the per-pipeline stage id/name.
_QUALITY_MAX_EFFORT = {
    "feature": ["verify", "drift-detect", "critique", "manager"],
    "bugfix": ["verify", "drift-detect", "critique", "manager"],
    "module-release": [
        "phase4-verify",
        "phase4b-drift-detect",
        "phase4c-critique",
        "phase5-manager",
    ],
}
_LATENCY_FAST_SPEED = {
    "feature": ["preflight", "policy"],
    "bugfix": ["preflight", "policy"],
    "module-release": ["preflight-priority"],
}


def _load_stages(pipeline_name: str) -> dict[str, dict]:
    """Return {stage_name_or_id: stage_dict} for a pipeline YAML.

    feature/bugfix key stages by `name`; module-release uses the id/needs
    DAG schema and keys by `id`.
    """
    data = yaml.safe_load((_PIPELINES_DIR / f"{pipeline_name}.yaml").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for stage in data.get("stages") or []:
        key = stage.get("name") or stage.get("id")
        assert key, f"{pipeline_name}: stage missing both name and id: {stage!r}"
        out[key] = stage
    return out


def test_payload_exists_at_expected_location() -> None:
    """The bundled payload must live where the skill's SKILL.md says it does."""
    assert DEFAULT_PAYLOAD.is_dir(), (
        f"Bundled payload missing at {DEFAULT_PAYLOAD}. "
        "SKILL.md step 3 references this path as the source of truth."
    )
    assert (DEFAULT_PAYLOAD / "pipelines").is_dir()
    assert (DEFAULT_PAYLOAD / "scripts").is_dir()


def test_scaffold_into_fresh_project_writes_expected_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = scaffold(project)

    pipelines = project / ".pipelines"
    policy = project / "scripts" / "policy"
    assert pipelines.is_dir()
    assert policy.is_dir()

    # Same expected files asserted by tests/test_cleanroom_smoke.py.
    # If you change one, change both.
    expected_pipeline_files = [
        pipelines / "roles" / "manifest-drafter.md",
        pipelines / "roles" / "researcher.md",
        pipelines / "roles" / "planner.md",
        pipelines / "roles" / "executor.md",
        pipelines / "roles" / "verifier.md",
        pipelines / "roles" / "drift-detector.md",
        pipelines / "roles" / "critic.md",
        pipelines / "roles" / "manager.md",
        pipelines / "roles" / "judge.md",
        pipelines / "feature.yaml",
        pipelines / "bugfix.yaml",
        pipelines / "module-release.yaml",
        pipelines / "manifest-template.yaml",
        pipelines / "action-classification.yaml",
        pipelines / "self-classification-rules.md",
    ]
    missing = [str(p.relative_to(project)) for p in expected_pipeline_files if not p.is_file()]
    assert not missing, f"missing scaffold files: {missing}"

    expected_policy_files = [
        policy / "check_manifest_schema.py",
        policy / "check_allowed_paths.py",
        policy / "check_no_todos.py",
        policy / "check_adr_gate.py",
        policy / "auto_promote.py",
    ]
    missing_policy = [str(p.relative_to(project)) for p in expected_policy_files if not p.is_file()]
    assert not missing_policy, f"missing policy files: {missing_policy}"


def test_scaffold_updates_gitignore(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = scaffold(project)

    assert result.gitignore_updated is True
    assert ".agent-runs/" in (project / ".gitignore").read_text(encoding="utf-8")


def test_scaffold_preserves_existing_gitignore_entries(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")

    scaffold(project)

    content = (project / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".env" in content
    assert ".agent-runs/" in content


def test_scaffold_idempotent_when_gitignore_already_has_entry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8")

    result = scaffold(project)

    assert result.gitignore_updated is False


def test_scaffold_refuses_to_overwrite_existing_pipelines(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".pipelines").mkdir()
    (project / ".pipelines" / "stale.txt").write_text("old", encoding="utf-8")

    with pytest.raises(ScaffoldError, match=".pipelines/ already exists"):
        scaffold(project)

    # Existing content must be untouched on refusal.
    assert (project / ".pipelines" / "stale.txt").read_text(encoding="utf-8") == "old"


def test_scaffold_overwrite_replaces_pipelines(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".pipelines").mkdir()
    (project / ".pipelines" / "stale.txt").write_text("old", encoding="utf-8")
    (project / "scripts").mkdir()
    (project / "scripts" / "policy").mkdir()
    (project / "scripts" / "policy" / "stale.py").write_text("old", encoding="utf-8")

    scaffold(project, overwrite=True)

    assert not (project / ".pipelines" / "stale.txt").exists()
    assert not (project / "scripts" / "policy" / "stale.py").exists()
    assert (project / ".pipelines" / "roles" / "manifest-drafter.md").is_file()


def test_scaffold_rejects_missing_project_root(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ScaffoldError, match="does not exist"):
        scaffold(missing)


def test_scaffold_rejects_missing_payload(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ScaffoldError, match="payload not found"):
        scaffold(project, payload_root=tmp_path / "no-payload")


# ---------------------------------------------------------------------------
# v3.0.0 WS-1: per-stage model / effort / speed binding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pipeline_name", ["feature", "bugfix", "module-release"])
def test_pipeline_yaml_parses_with_optional_hints(pipeline_name: str) -> None:
    """Every pipeline YAML still parses, and any model/effort/speed hint that
    is present carries a valid value. A YAML *with* hints (most stages) and a
    YAML *without* them (stages that omit the keys) both round-trip cleanly."""
    stages = _load_stages(pipeline_name)
    assert stages, f"{pipeline_name}: no stages parsed"
    for name, stage in stages.items():
        if "effort" in stage:
            assert stage["effort"] in _VALID_EFFORT, (
                f"{pipeline_name}.{name}: effort={stage['effort']!r} not in {_VALID_EFFORT}"
            )
        if "speed" in stage:
            assert stage["speed"] in _VALID_SPEED, (
                f"{pipeline_name}.{name}: speed={stage['speed']!r} not in {_VALID_SPEED}"
            )
        # model is allowed but no model id is hardcoded anywhere (the v3
        # migration is behavioral) — it must stay unset.
        assert "model" not in stage, (
            f"{pipeline_name}.{name}: a model id is hardcoded; v3 keeps model unset"
        )


@pytest.mark.parametrize("pipeline_name", ["feature", "bugfix", "module-release"])
def test_quality_stages_bias_to_max_effort(pipeline_name: str) -> None:
    """Quality-critical stages (critic, verifier, drift-detector, manager)
    carry effort: max so a higher-effort cold read is requested."""
    stages = _load_stages(pipeline_name)
    for name in _QUALITY_MAX_EFFORT[pipeline_name]:
        assert name in stages, f"{pipeline_name}: expected stage {name} missing"
        assert stages[name].get("effort") == "max", (
            f"{pipeline_name}.{name}: expected effort: max, got {stages[name].get('effort')!r}"
        )


@pytest.mark.parametrize("pipeline_name", ["feature", "bugfix", "module-release"])
def test_latency_stages_use_fast_speed(pipeline_name: str) -> None:
    """Latency-sensitive command stages (preflight, policy) carry speed: fast."""
    stages = _load_stages(pipeline_name)
    for name in _LATENCY_FAST_SPEED[pipeline_name]:
        assert name in stages, f"{pipeline_name}: expected stage {name} missing"
        assert stages[name].get("speed") == "fast", (
            f"{pipeline_name}.{name}: expected speed: fast, got {stages[name].get('speed')!r}"
        )


def test_stage_without_hints_preserves_defaults() -> None:
    """A stage that omits the hint keys carries none of them, so it spawns
    with the orchestrator's defaults — the additive contract that keeps
    pre-v3 behavior intact. `research` is the canonical no-hint stage."""
    feature = _load_stages("feature")
    research = feature["research"]
    assert not ({"model", "effort", "speed"} & research.keys()), (
        f"research should carry no execution hints, got {research!r}"
    )
    # execute is the other load-bearing no-hint stage.
    execute = feature["execute"]
    assert not ({"model", "effort", "speed"} & execute.keys())


def test_run_md_passes_stage_hints_to_subagent() -> None:
    """The orchestrator instruction must tell the run skill to read and pass
    the per-stage model/effort/speed hints when spawning a stage subagent."""
    text = _RUN_MD.read_text(encoding="utf-8")
    assert "model" in text and "effort" in text and "speed" in text
    assert "execution hints" in text, "run.md must document the WS-1 hint contract"


def test_run_md_documents_risk_driven_escalation() -> None:
    """run.md must document that manifest risk: high raises model-stage effort
    — the risk-driven escalation path."""
    text = _RUN_MD.read_text(encoding="utf-8")
    assert "Risk-driven effort escalation" in text
    assert "risk: high" in text
    # high-risk runs escalate to at least `extra`.
    assert "extra" in text


def test_run_md_spawns_drafter_at_max_effort() -> None:
    """The manifest-drafter has no pipeline-YAML stage, so its quality-critical
    effort hint must live in run.md Step 4."""
    text = _RUN_MD.read_text(encoding="utf-8")
    assert "manifest-drafter is quality-critical" in text
    assert "effort: max" in text
