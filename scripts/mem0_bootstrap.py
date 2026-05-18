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


def cmd_init(args: argparse.Namespace) -> int:
    """`pipeline mem0 init` - create .mem0/config.json + consent file."""
    root = _project_root()
    config_dir = root / ".mem0"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    if config_path.exists() and not args.force:
        print(f"mem0_bootstrap: config already exists at {config_path}. Pass --force to overwrite.")
        return 1

    template_path = root / "pipelines" / "mem0-config-template.json"
    if not template_path.exists():
        print(f"mem0_bootstrap: template not found at {template_path}.", file=sys.stderr)
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
    """`pipeline mem0 up` - docker compose up the OSS stack."""
    root = _project_root()
    config = load_config(root)
    if config.mode != "oss":
        print("mem0_bootstrap: `up` is OSS-only. Switch mode to oss or use platform.")
        return 1
    compose_dir = Path(config.oss.compose_dir)
    if not compose_dir.is_absolute():
        compose_dir = root / compose_dir
    if not compose_dir.exists():
        print(f"mem0_bootstrap: compose dir not found at {compose_dir}. Vendor mem0/server/ first.", file=sys.stderr)
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
    """`pipeline mem0 test` - smoke check: config + identity + adapter list_entities."""
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

    Lists candidates by age and asks for explicit confirmation before any
    delete. Not implemented as a one-shot for safety - dry-run is the
    default; pass --execute to actually delete.
    """
    root = _project_root()
    config = load_config(root)
    if not config.enabled:
        print("mem0_bootstrap: prune - Mem0 not enabled; nothing to prune.")
        return 1
    print(
        "mem0_bootstrap: prune - candidate listing.\n"
        f"  run_id memories older than {config.hygiene.prune_run_id_after_days} days will be flagged.\n"
        f"  session_state memories older than {config.hygiene.prune_session_state_after_days} days will be flagged.\n"
        f"  anti_pattern + decision memories older than {config.hygiene.review_long_lived_after_days} days are listed for review (not deleted)."
    )
    if not args.execute:
        print("mem0_bootstrap: prune dry-run complete. Pass --execute to actually delete (requires interactive confirm per FR-12).")
        return 0
    print("mem0_bootstrap: prune execute mode not yet implemented; requires interactive operator confirmation per FR-12. Stop.")
    return 2


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
    p_prune.add_argument("--execute", action="store_true", help="Actually delete; default is dry-run")
    p_prune.set_defaults(func=cmd_prune)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
