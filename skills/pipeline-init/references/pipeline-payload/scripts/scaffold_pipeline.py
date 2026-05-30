"""Scaffold .pipelines/ + scripts/policy/ from the bundled payload.

Pure-stdlib helper that performs the deterministic part of
`/agent-pipeline-claude:pipeline-init` step 3 — copying the bundled
payload at `skills/pipeline-init/references/pipeline-payload/` into a
target project root. Used by `tests/test_scaffold_pipeline.py` for a
$0 deterministic verification of the scaffold's file set, and callable
directly as a CLI for anyone who wants a one-shot scaffold.

Public API:

    scaffold(project_root: Path, *, payload_root: Path | None = None,
             overwrite: bool = False) -> ScaffoldResult

CLI:

    python scripts/scaffold_pipeline.py <project_root> [--overwrite]

Hard rules (mirrors SKILL.md):

- Never overwrite an existing `.pipelines/` unless `overwrite=True`.
- Never write outside `project_root`.
- Never touch the plugin's marketplace cache.
- CLAUDE.md is NOT scaffolded here — that step needs LLM judgment to
  draft project-specific orientation copy. This helper covers the
  deterministic file-copy portion only.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PAYLOAD = (
    HERE.parent / "skills" / "pipeline-init" / "references" / "pipeline-payload"
)


@dataclass
class ScaffoldResult:
    pipelines_files: list[Path] = field(default_factory=list)
    policy_files: list[Path] = field(default_factory=list)
    gitignore_updated: bool = False

    @property
    def all_files(self) -> list[Path]:
        return [*self.pipelines_files, *self.policy_files]


class ScaffoldError(RuntimeError):
    pass


def _copy_tree(src: Path, dst: Path) -> list[Path]:
    written: list[Path] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written.append(target)
    return written


def _ensure_gitignore_has_agent_runs(project_root: Path) -> bool:
    gitignore = project_root / ".gitignore"
    needle = ".agent-runs/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if needle in content:
            return False
        sep = "" if content.endswith("\n") or content == "" else "\n"
        gitignore.write_text(content + sep + needle + "\n", encoding="utf-8")
        return True
    gitignore.write_text(needle + "\n", encoding="utf-8")
    return True


def scaffold(
    project_root: Path,
    *,
    payload_root: Path | None = None,
    overwrite: bool = False,
) -> ScaffoldResult:
    project_root = project_root.resolve()
    payload = (payload_root or DEFAULT_PAYLOAD).resolve()

    if not payload.is_dir():
        raise ScaffoldError(f"payload not found: {payload}")
    if not (payload / "pipelines").is_dir() or not (payload / "scripts").is_dir():
        raise ScaffoldError(
            f"payload missing pipelines/ or scripts/: {payload}"
        )
    if not project_root.exists():
        raise ScaffoldError(f"project_root does not exist: {project_root}")
    if not project_root.is_dir():
        raise ScaffoldError(f"project_root is not a directory: {project_root}")

    pipelines_dst = project_root / ".pipelines"
    policy_dst = project_root / "scripts" / "policy"

    if pipelines_dst.exists() and not overwrite:
        raise ScaffoldError(
            f".pipelines/ already exists at {pipelines_dst}; "
            "pass overwrite=True (or --overwrite) to re-init"
        )
    if policy_dst.exists() and not overwrite:
        raise ScaffoldError(
            f"scripts/policy/ already exists at {policy_dst}; "
            "pass overwrite=True (or --overwrite) to re-init"
        )

    if overwrite and pipelines_dst.exists():
        shutil.rmtree(pipelines_dst)
    if overwrite and policy_dst.exists():
        shutil.rmtree(policy_dst)

    pipelines_written = _copy_tree(payload / "pipelines", pipelines_dst)
    policy_written = _copy_tree(payload / "scripts", policy_dst)
    gitignore_updated = _ensure_gitignore_has_agent_runs(project_root)

    return ScaffoldResult(
        pipelines_files=pipelines_written,
        policy_files=policy_written,
        gitignore_updated=gitignore_updated,
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scaffold_pipeline",
        description="Scaffold .pipelines/ + scripts/policy/ from bundled payload",
    )
    parser.add_argument("project_root", type=Path, help="target project root")
    parser.add_argument(
        "--overwrite", action="store_true", help="re-init: remove existing dirs first"
    )
    parser.add_argument(
        "--payload-root",
        type=Path,
        default=None,
        help=f"override payload location (default: {DEFAULT_PAYLOAD})",
    )
    args = parser.parse_args(argv)

    try:
        result = scaffold(
            args.project_root,
            payload_root=args.payload_root,
            overwrite=args.overwrite,
        )
    except ScaffoldError as exc:
        print(f"scaffold_pipeline: {exc}", file=sys.stderr)
        return 1

    print(
        f"Scaffolded {len(result.pipelines_files)} pipeline files + "
        f"{len(result.policy_files)} policy files into {args.project_root}"
    )
    if result.gitignore_updated:
        print("Updated .gitignore with .agent-runs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
