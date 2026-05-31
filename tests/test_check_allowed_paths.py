# SPDX-License-Identifier: Apache-2.0
"""Adversarial coverage for the allowed-paths write-scope gate (audit TEST-001).

The audit measured this gate at 19% — its bypass branches (forbidden-path hit,
outside-allowed, the v3.0.0 `..`/absolute anomaly, and the hand-rolled manifest
parser edges) had no direct test, so a refactor that made the gate fail open
would ship green. These import-and-call tests pin the exit-code surface.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_allowed_paths as cap  # noqa: E402
import policy_utils  # noqa: E402


# --- _is_under: the core scope predicate ------------------------------------


def test_is_under_exact_and_prefix():
    assert cap._is_under("src/a.py", ["src/"]) is True
    assert cap._is_under("src", ["src"]) is True
    assert cap._is_under("src/deep/a.py", ["src"]) is True


def test_is_under_rejects_sibling_prefix_confusion():
    # "src2" must NOT be considered under "src"
    assert cap._is_under("src2/a.py", ["src"]) is False
    assert cap._is_under("docs/a.md", ["src/"]) is False
    assert cap._is_under("anything", [""]) is False  # empty prefix never matches


# --- _load_manifest_lists: hand-rolled parser edges -------------------------


def test_parser_handles_comments_quotes_and_nested_keys(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(
        "pipeline_run:\n"
        '  goal: "do a thing"  # trailing comment\n'
        "  allowed_paths:\n"
        "    - src/foo.py\n"
        '    - "src/bar.py"   # quoted + comment\n'
        "    - 'tests/'\n"
        "  forbidden_paths:\n"
        "    - docs/adr/\n"
        "  non_goals:\n"
        "    - not-a-path\n",
        encoding="utf-8",
    )
    allowed, forbidden = cap._load_manifest_lists(m)
    assert allowed == ["src/foo.py", "src/bar.py", "tests/"]
    assert forbidden == ["docs/adr/"]
    # the `- not-a-path` under non_goals must NOT leak into either list
    assert "not-a-path" not in allowed and "not-a-path" not in forbidden


def test_parser_handles_empty_inline_list(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(
        "pipeline_run:\n"
        "  allowed_paths: []\n"
        "  forbidden_paths:\n"
        "    - secrets/\n",
        encoding="utf-8",
    )
    allowed, forbidden = cap._load_manifest_lists(m)
    assert allowed == []
    assert forbidden == ["secrets/"]


# --- main(): the exit-code bypass surface -----------------------------------

_MANIFEST = (
    "pipeline_run:\n"
    "  allowed_paths:\n"
    "    - src/\n"
    "  forbidden_paths:\n"
    "    - src/secrets/\n"
)


def _run_main(monkeypatch, tmp_path, manifest_text, changed):
    run_dir = tmp_path / ".agent-runs"
    (run_dir / "test-run").mkdir(parents=True)
    (run_dir / "test-run" / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    monkeypatch.setattr(cap, "RUN_DIR", run_dir)
    monkeypatch.setattr(cap, "_git_changed_files", lambda: changed)
    # Keep the single-repo path (skip the multi-repo-admin degraded-PASS branch).
    monkeypatch.setattr(policy_utils, "read_project_shape", lambda root: "single-repo", raising=False)
    monkeypatch.setattr(policy_utils, "is_git_repo", lambda root: True, raising=False)
    monkeypatch.setattr(sys, "argv", ["check_allowed_paths", "--run", "test-run"])
    return cap.main()


def test_forbidden_path_hit_returns_1(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, _MANIFEST, ["src/secrets/key.py"]) == 1


def test_outside_allowed_returns_1(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, _MANIFEST, ["docs/readme.md"]) == 1


def test_dotdot_traversal_anomaly_returns_1(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, _MANIFEST, ["src/../../../etc/passwd"]) == 1


def test_absolute_path_anomaly_returns_1(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, _MANIFEST, ["/etc/passwd"]) == 1


def test_in_scope_change_returns_0(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, _MANIFEST, ["src/module/x.py"]) == 0


def test_no_constraints_is_noop_pass(monkeypatch, tmp_path):
    empty = "pipeline_run:\n  goal: x\n"
    assert _run_main(monkeypatch, tmp_path, empty, ["anything/at/all.py"]) == 0
