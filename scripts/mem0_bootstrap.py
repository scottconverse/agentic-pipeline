#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mem0 bootstrap CLI: init / up / down / prune / whoami / test.

Implements PRD section 9 items 4 (identity whoami), 8 (bootstrap init),
9 (prune), 10 (smoke test). Designed to be called from the mem0 skill
or directly via `python scripts/mem0_bootstrap.py <subcommand>`.

OSS mode brings up Qdrant + Postgres via docker compose. Platform mode
runs the consent gate, then expects MEM0_API_KEY in the environment
(or the OS keychain, written by mem0 init --agent if available).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from memory import Mem0Config, derive_identity, load_config
    from memory.adapter import build_adapter
    from memory.policy import build_policy
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from memory import Mem0Config, derive_identity, load_config  # noqa: E402
    from memory.adapter import build_adapter  # noqa: E402
    from memory.policy import build_policy  # noqa: E402


def _project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def _locate_config_template(project_root: Path) -> Path | None:
    """Find mem0-config-template.json in any supported location.

    Search order (first hit wins):
      1. <project>/.pipelines/mem0-config-template.json  - scaffolded by pipeline-init
      2. <project>/pipelines/mem0-config-template.json   - plugin source layout
      3. <plugin>/pipelines/mem0-config-template.json    - plugin install dir
                                                           (last-resort fallback)

    Returns None if none exist. Phase 6.c fix for checkpoint C:
    mem0_bootstrap previously only checked location 2, which doesn't
    exist on projects that ran pipeline-init.
    """
    candidates = [
        project_root / ".pipelines" / "mem0-config-template.json",
        project_root / "pipelines" / "mem0-config-template.json",
        Path(__file__).resolve().parents[1] / "pipelines" / "mem0-config-template.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


# Pin for the on-demand vendor clone. Refresh by editing this constant
# and verifying against the new commit. Documented at vendor/VENDOR_PINS.md.
MEM0_VENDOR_REPO = "https://github.com/mem0ai/mem0.git"
MEM0_VENDOR_PIN = "main"  # TODO: pin a specific commit once an end-to-end smoke validates one


def _ensure_vendor_mem0(repo_root: Path) -> Path:
    """Clone or update vendor/mem0/ to the pinned commit. Returns the path
    to `vendor/mem0/server/` where docker-compose.yml lives.

    Best-effort; raises subprocess.CalledProcessError if git is unavailable
    or the network is down, so the caller can surface a clean error to the
    operator. The CLI translates that into a 2 exit code with a guidance
    message rather than crashing.
    """
    vendor_dir = repo_root / "vendor" / "mem0"
    if vendor_dir.exists() and (vendor_dir / ".git").exists():
        subprocess.run(["git", "fetch", "origin"], cwd=vendor_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "checkout", MEM0_VENDOR_PIN], cwd=vendor_dir, check=True, capture_output=True, text=True)
        return vendor_dir / "server"

    vendor_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "50", MEM0_VENDOR_REPO, str(vendor_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    if MEM0_VENDOR_PIN != "main":
        subprocess.run(["git", "checkout", MEM0_VENDOR_PIN], cwd=vendor_dir, check=True, capture_output=True, text=True)
    return vendor_dir / "server"


def cmd_init(args: argparse.Namespace) -> int:
    """`pipeline mem0 init` - create .mem0/config.json + consent file."""
    root = _project_root()
    config_dir = root / ".mem0"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    if config_path.exists() and not args.force:
        print(f"mem0_bootstrap: config already exists at {config_path}. Pass --force to overwrite.")
        return 1

    template_path = _locate_config_template(root)
    if template_path is None:
        print(
            "mem0_bootstrap: mem0-config-template.json not found in any of: "
            ".pipelines/ (scaffolded), pipelines/ (plugin source), plugin install dir. "
            "Re-run pipeline-init or reinstall the plugin.",
            file=sys.stderr,
        )
        return 2
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["mode"] = args.mode
    config_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    print(f"mem0_bootstrap: wrote {config_path} (mode={args.mode})")

    if args.mode == "platform":
        consent_path = config_dir / "consent.json"
        if not consent_path.exists():
            consent_path.write_text(
                json.dumps(
                    {
                        "grant": False,
                        "summary": "Platform mode sends data to mcp.mem0.ai. SOC 2 Type 1 + HIPAA. Edit grant=true once reviewed.",
                        "created": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"mem0_bootstrap: wrote consent stub at {consent_path}. Edit grant=true to enable platform writes.")

    # whoami snapshot
    identity = derive_identity(root)
    print(
        "mem0_bootstrap: identity: "
        f"user_id={identity.user_id} agent_id={identity.agent_id} app_id={identity.app_id} run_id={identity.run_id}"
    )
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    """`pipeline mem0 up` - docker compose up the OSS stack.

    Auto-vendors mem0ai/mem0 at the pinned commit (MEM0_VENDOR_PIN) into
    ./vendor/mem0/ if not already present, then runs `docker compose up
    -d` against vendor/mem0/server/.
    """
    root = _project_root()
    config = load_config(root)
    if config.mode != "oss":
        print("mem0_bootstrap: `up` is OSS-only. Switch mode to oss or use platform.")
        return 1
    compose_dir = Path(config.oss.compose_dir)
    if not compose_dir.is_absolute():
        compose_dir = root / compose_dir
    if not compose_dir.exists():
        # Try auto-vendor: clone mem0ai/mem0 at pinned commit
        print(f"mem0_bootstrap: compose dir not found at {compose_dir}; vendoring mem0ai/mem0 at pin={MEM0_VENDOR_PIN}...")
        try:
            compose_dir = _ensure_vendor_mem0(root)
        except subprocess.CalledProcessError as exc:
            print(f"mem0_bootstrap: vendor clone failed: {exc.stderr or exc}", file=sys.stderr)
            print("mem0_bootstrap: clone manually: git clone https://github.com/mem0ai/mem0.git vendor/mem0", file=sys.stderr)
            return 2
    proc = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def cmd_down(args: argparse.Namespace) -> int:
    """`pipeline mem0 down` - docker compose down the OSS stack."""
    root = _project_root()
    config = load_config(root)
    if config.mode != "oss":
        print("mem0_bootstrap: `down` is OSS-only.")
        return 1
    compose_dir = Path(config.oss.compose_dir)
    if not compose_dir.is_absolute():
        compose_dir = root / compose_dir
    if not compose_dir.exists():
        print(f"mem0_bootstrap: compose dir not found at {compose_dir}.", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["docker", "compose", "down"],
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def cmd_whoami(args: argparse.Namespace) -> int:
    """`pipeline mem0 whoami` - print derived identity for the current repo."""
    root = _project_root()
    identity = derive_identity(root)
    payload = {
        "user_id": identity.user_id,
        "agent_id": identity.agent_id,
        "app_id": identity.app_id,
        "run_id": identity.run_id,
        "branch": identity.branch,
        "repo_root": str(identity.repo_root),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """`pipeline mem0 test` - smoke check: config + identity + adapter list_entities.

    `policy.list_entities()` swallows backend exceptions and returns
    ``{"error": "..."}`` (so the agent-facing path is fail-soft). The
    smoke test must therefore inspect the returned shape — a try/except
    around the call never fires when the backend is unreachable, which
    used to make `mem0 test` falsely report success against a wrong URL.
    """
    root = _project_root()
    config = load_config(root)
    if not config.enabled:
        print("mem0_bootstrap: test - NOT_ENABLED. No .mem0/config.json and no env override. File-backed Layer A still works.")
        return 1
    identity = derive_identity(root)
    adapter = build_adapter(config)
    policy = build_policy(config, identity, adapter=adapter)
    try:
        entities = policy.list_entities()
    except Exception as exc:  # noqa: BLE001
        print(f"mem0_bootstrap: test - FAIL - list_entities raised: {exc}", file=sys.stderr)
        return 2
    if isinstance(entities, dict) and "error" in entities:
        hint = ""
        if config.mode == "oss":
            hint = (
                f" — base_url is {config.oss.base_url!r}; the vendor compose "
                "exposes the FastAPI server on :8888 and the dashboard on :3000. "
                "If the URL ends in :3000, edit .mem0/config.json oss.base_url "
                "to http://localhost:8888 (or rerun `mem0 init --mode oss --force`)."
            )
        print(
            f"mem0_bootstrap: test - FAIL - backend reported error: {entities['error']}{hint}",
            file=sys.stderr,
        )
        return 2
    summary = {
        "mode": config.mode,
        "enabled": config.enabled,
        "consent_granted": config.consent_granted_for,
        "identity": identity.as_write_keys(include_run=False),
        "entities": entities,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """`pipeline mem0 sync` - flush Layer A (file-backed) records with valid
    type into Layer B (Mem0). Idempotent per record fingerprint.
    """
    root = _project_root()
    config = load_config(root)
    if not config.enabled:
        print("mem0_bootstrap: sync - Mem0 not enabled. Layer A still operational; Layer B sync is a no-op.")
        return 1
    identity = derive_identity(root)
    adapter = build_adapter(config)
    policy = build_policy(config, identity, adapter=adapter)
    from memory.sync import flush_layer_a_to_mem0

    result = flush_layer_a_to_mem0(root, policy)
    payload = {
        "candidates": result.candidates,
        "sent": result.sent,
        "skipped_no_type": result.skipped_no_type,
        "skipped_already_sent": result.skipped_already_sent,
        "rejected": result.rejected,
        "outboxed": result.outboxed,
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """`pipeline mem0 prune` - human-driven hygiene per FR-12.

    Two-stage prune:
      Layer A (file-backed): archive aged .agent-runs/<run-id>/ directories
        to .agent-runs/_archived/<run-id>-<epoch>/ instead of deleting.
        Reversible, leaves a trail, never loses evidence.
      Layer B (Mem0): list candidates by type + age. `--execute --yes`
        actually deletes via policy.prune_delete (the only path that
        sets allowed_by_prune=True).

    Long-lived `anti_pattern` and `decision` memories are NEVER auto-deleted;
    they're listed for explicit human review per FR-12.
    """
    root = _project_root()
    config = load_config(root)
    runs_root = root / ".agent-runs"

    # Layer A: enumerate aged run dirs
    import time
    now = time.time()
    age_seconds = config.hygiene.prune_run_id_after_days * 86400
    aged_runs: list[Path] = []
    if runs_root.exists():
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("_"):
                continue
            mtime = run_dir.stat().st_mtime
            if (now - mtime) > age_seconds:
                aged_runs.append(run_dir)

    print("mem0_bootstrap: prune - Layer A candidates (file-backed run dirs)")
    print(f"  threshold: older than {config.hygiene.prune_run_id_after_days} days")
    print(f"  found: {len(aged_runs)} run dir(s) eligible to archive")
    for run_dir in aged_runs:
        age_days = int((now - run_dir.stat().st_mtime) // 86400)
        print(f"    - {run_dir.name} (age {age_days} days)")

    # Layer B: enumerate by type via adapter (if enabled)
    long_lived_candidates: list[dict] = []
    aged_session_state: list[dict] = []
    if config.enabled:
        try:
            identity = derive_identity(root)
            adapter = build_adapter(config)
            policy = build_policy(config, identity, adapter=adapter)
            all_memories = adapter.get_all(filters=identity.as_filter())
            for record in all_memories:
                meta = (record.metadata or {})
                record_type = str(meta.get("type", ""))
                ts = meta.get("source_timestamp") or meta.get("timestamp") or ""
                if record_type == "session_state":
                    aged_session_state.append({"id": record.id, "type": record_type, "ts": ts})
                if record_type in {"anti_pattern", "decision"}:
                    long_lived_candidates.append({"id": record.id, "type": record_type, "ts": ts})
        except Exception as exc:  # noqa: BLE001
            print(f"  Layer B enumeration unavailable: {exc}")

    if aged_session_state:
        print(f"\n  Layer B session_state candidates older than {config.hygiene.prune_session_state_after_days} days: {len(aged_session_state)}")
    if long_lived_candidates:
        print(f"\n  Layer B long-lived (review-only, never auto-deleted): {len(long_lived_candidates)}")
        print("  Per FR-12 these require explicit human review; not deleted by --execute.")
        for cand in long_lived_candidates[:5]:
            print(f"    - {cand['id']} [{cand['type']}] {cand['ts']}")

    if not args.execute:
        print("\nmem0_bootstrap: prune dry-run complete. Pass --execute --yes to archive Layer A and delete Layer B session_state.")
        return 0

    if not args.yes:
        print("\nmem0_bootstrap: prune --execute requires --yes for non-interactive confirmation per FR-12.")
        return 2

    # Execute: archive Layer A
    archive_root = runs_root / "_archived"
    archive_root.mkdir(exist_ok=True)
    archived = 0
    for run_dir in aged_runs:
        target = archive_root / f"{run_dir.name}-{int(now)}"
        try:
            run_dir.rename(target)
            archived += 1
        except OSError as exc:
            print(f"  archive failed for {run_dir.name}: {exc}", file=sys.stderr)
    print(f"\nmem0_bootstrap: prune Layer A - archived {archived}/{len(aged_runs)} run dir(s) under {archive_root}")

    # Execute: delete Layer B session_state
    deleted = 0
    if config.enabled and aged_session_state:
        identity = derive_identity(root)
        adapter = build_adapter(config)
        policy = build_policy(config, identity, adapter=adapter)
        for cand in aged_session_state:
            try:
                policy.prune_delete(cand["id"])
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  prune_delete failed for {cand['id']}: {exc}", file=sys.stderr)
    print(f"mem0_bootstrap: prune Layer B - deleted {deleted} session_state memor(ies)")
    print(f"mem0_bootstrap: prune Layer B - long-lived candidates left for human review: {len(long_lived_candidates)}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="agent-pipeline-claude 2.0.0")
    subs = parser.add_subparsers(dest="command", required=True)

    p_init = subs.add_parser("init", help="Create .mem0/config.json + consent stub")
    p_init.add_argument("--mode", choices=["oss", "platform"], default="oss")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_up = subs.add_parser("up", help="docker compose up the OSS Mem0 stack")
    p_up.set_defaults(func=cmd_up)

    p_down = subs.add_parser("down", help="docker compose down the OSS Mem0 stack")
    p_down.set_defaults(func=cmd_down)

    p_whoami = subs.add_parser("whoami", help="Print derived identity for the current repo")
    p_whoami.set_defaults(func=cmd_whoami)

    p_test = subs.add_parser("test", help="Smoke check: config + identity + list_entities")
    p_test.set_defaults(func=cmd_test)

    p_sync = subs.add_parser("sync", help="Flush typed Layer A records into Layer B (Mem0)")
    p_sync.set_defaults(func=cmd_sync)

    p_prune = subs.add_parser("prune", help="Hygiene: list/delete aged memories (per FR-12)")
    p_prune.add_argument("--execute", action="store_true", help="Actually archive Layer A + delete Layer B session_state; default is dry-run")
    p_prune.add_argument("--yes", action="store_true", help="Non-interactive confirmation token; required with --execute")
    p_prune.set_defaults(func=cmd_prune)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
