# SPDX-License-Identifier: Apache-2.0
# Ported from agent-pipeline-codex v0.9.0 (tests/test_execute_readiness.py).

from pathlib import Path

from scripts import check_execute_readiness


def _run_dir(tmp_path: Path, monkeypatch, run_id: str = "readiness-run") -> Path:
    monkeypatch.setattr(check_execute_readiness, "REPO_ROOT", tmp_path)
    run_dir = tmp_path / ".agent-runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def test_execute_readiness_accepts_ready_zero_blocker_report(tmp_path, monkeypatch) -> None:
    run_dir = _run_dir(tmp_path, monkeypatch)
    (run_dir / "implementation-report.md").write_text(
        "\n".join(
            [
                "## 0. Pre-verify DoD Readiness Gate",
                "",
                "**DoD readiness: READY**",
                "**DoD checklist: 4 total, 3 ready, 0 blocked, 1 deferred**",
                "",
                "- [x] Backend implemented and tested.",
                "- [x] UI browser evidence captured.",
                "- [x] Docs updated.",
                "- [x] External TSA proof intentionally deferred by director-decisions.md.",
            ]
        ),
        encoding="utf-8",
    )

    assert check_execute_readiness.check_execute_readiness("readiness-run") == []


def test_execute_readiness_rejects_not_ready_report(tmp_path, monkeypatch) -> None:
    run_dir = _run_dir(tmp_path, monkeypatch)
    (run_dir / "implementation-report.md").write_text(
        "\n".join(
            [
                "## 0. Pre-verify DoD Readiness Gate",
                "",
                "**DoD readiness: NOT_READY**",
                "**DoD checklist: 4 total, 3 ready, 1 blocked, 0 deferred**",
                "",
                "- [ ] Operator UX not implemented.",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_execute_readiness.check_execute_readiness("readiness-run")

    assert any("NOT_READY" in violation for violation in violations)
    assert any("1 blocked" in violation for violation in violations)
    assert any("unchecked readiness boxes" in violation for violation in violations)


def test_execute_readiness_rejects_legacy_backend_only_report(tmp_path, monkeypatch) -> None:
    run_dir = _run_dir(tmp_path, monkeypatch)
    (run_dir / "implementation-report.md").write_text(
        "pytest output: 31 passed, 0 failed",
        encoding="utf-8",
    )

    violations = check_execute_readiness.check_execute_readiness("readiness-run")

    assert any("missing exact readiness line" in violation for violation in violations)
    assert any("missing parseable checklist line" in violation for violation in violations)
