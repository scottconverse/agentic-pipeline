---
name: mem0
description: Manage the v2.0 Mem0 cross-session memory layer. Subcommands - init (create config + consent stub), up/down (start/stop OSS docker stack), whoami (show derived identity for this repo), test (smoke check), prune (hygiene). Invoked as /agent-pipeline-claude:mem0 <subcommand>.
---

# mem0 - cross-session memory layer

Manages the Mem0 layer that sits on top of file-backed run memory
(`.agent-runs/<run-id>/memory/`). Mem0 is the cross-session, semantically
retrievable knowledge layer: decisions made in one session are recalled
in the next.

## Subcommands

Pick the subcommand from `$ARGUMENTS`:

- `init` — write `.mem0/config.json` from the template and (for platform
  mode) a consent stub at `.mem0/consent.json`. Operator must edit
  `consent.json` and set `grant: true` before platform writes happen.
- `up` — `docker compose up -d` the OSS stack (Qdrant + Postgres) from
  `vendor/mem0/server/`. OSS mode only.
- `down` — `docker compose down` the OSS stack. OSS mode only.
- `whoami` — print derived identity (user_id, agent_id, app_id, run_id,
  branch, repo_root) for the current repo.
- `test` — smoke check: load config, derive identity, call
  `list_entities()` against the configured adapter. Confirms backend
  reachable.
- `prune` — list aging memories per FR-12 hygiene policy. Dry-run by
  default; `--execute` requires interactive confirmation.

## Workflow

For each subcommand, invoke the bootstrap CLI with the Bash tool:

```bash
python scripts/mem0_bootstrap.py <subcommand> [flags]
```

Report the CLI's output to the operator verbatim. Do not modify
`.mem0/config.json` directly — always go through `mem0 init` so the
template stays canonical.

## Hard rules

- This skill is operator-facing. The agent never invokes `prune --execute`
  on its own.
- Per FR-8, the agent must never call any `delete_*` operation directly;
  delete goes through `prune` with explicit human confirmation.
- Per FR-14, platform mode requires `.mem0/consent.json` with
  `grant: true` before any backend write. If consent is missing, writes
  fall through to the local outbox at `.mem0/outbox/`.
- Platform mode requires `MEM0_API_KEY` in the environment. If missing,
  the adapter returns NullAdapter (no-op) - the agent still gets file-
  backed Layer A memory.
- Layer A (file-backed under `.agent-runs/<run-id>/memory/`) is
  unconditional and runs without docker, network, or any config.
  Mem0 is the cross-session bridge on top of Layer A.

## Two-layer architecture

  Layer A (file-backed): .agent-runs/<run-id>/memory/*.jsonl
    Unconditional. Written by hooks (Phase 4). Per-run, no network.

  Layer B (Mem0): managed Platform OR self-hosted OSS via docker compose.
    Best-effort. Behind a circuit breaker. Source of truth for
    cross-session knowledge.

Layer A is the safety floor: hooks always write to it regardless of
Mem0 status. Layer B is the durable cross-session bridge.
