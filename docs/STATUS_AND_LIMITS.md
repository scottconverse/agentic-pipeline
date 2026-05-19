# agent-pipeline-claude — Status & Limits

This document is the **honest** map of what works, what's experimental, and what's known broken in the current release (v2.0.0, shipped 2026-05-18). Treat README and CHANGELOG as marketing-adjacent; treat this file as the source of truth. When the plugin's surface and these tables disagree, the tables are wrong and should be opened as an issue — the rationale is that "code path present" never equals "feature is supported," and this file is where that distinction is made durable.

## Status legend
- **Stable** — exercised continuously, has tests, used in production.
- **Beta** — works for the documented happy path; rough edges in error cases or shapes not yet exercised.
- **Experimental** — code path is present and wired up, but accumulates value only with usage; no claim of accuracy.
- **Broken / disabled** — known issue or deprecated shim; do not rely on.

Test count at this release: **289 passed, 1 skipped** (`tests/test_cleanroom_smoke.py` — needs `ANTHROPIC_API_KEY` to exercise a real model end-to-end).

---

## Skills (operator-facing slash commands)

| Skill | Status | Notes |
|---|---|---|
| `/agent-pipeline-claude:run` | Stable | Orchestration entry point. Drafts manifest, three modal gates, end-to-end stage flow, resume + status. Canonical procedure in `skills/run/references/run.md`. |
| `/agent-pipeline-claude:pipeline-init` | Stable | Scaffolds `.pipelines/`, `scripts/policy/`, starter `CLAUDE.md`. Covered by `test_skill_packaging`, `test_cleanroom_install`, `test_scaffold_pipeline`. |
| `/agent-pipeline-claude:show-run-status` | Stable | Read-only summary of a run from `.agent-runs/<run-id>/`. Covered by `test_show_run_status` (3 tests). |
| `/agent-pipeline-claude:intake` | Beta | New in v2.0 (Phase 3). Drafts intake artifacts + writes `active-control-state.md` with `active_run: drafting` (bridge model, audit Pass 12). Happy path tested; unusual project shapes (multi-monorepo, non-standard spec locations) not yet exercised. |
| `/agent-pipeline-claude:mem0` | Beta | OSS mode happy path works after the Pass 1 port fix (`:8888` API vs `:3000` dashboard). Platform mode requires `.mem0/consent.json` with `grant: true` before any backend write; the grant flow has been tested but not yet exercised against a real long-running Platform install. |
| `/agent-pipeline-claude:audit-init` | Beta | v0.3 dual-AI handoff scaffolder, preserved through v2.0. Happy-path scaffolding works; no end-to-end automated test; cross-runtime wiring (non-Claude auditor) requires manual integration. |
| `/agent-pipeline-claude:run-autonomous` | Broken / disabled | Deprecated no-op shim since v1.3.0. Skill description says so; `tests/test_v130_redesign.py::test_check_autonomous_mode_is_noop` pins it. Use `/agent-pipeline-claude:run` instead. |
| `/agent-pipeline-claude:grant-autonomous` | Broken / disabled | Deprecated no-op shim since v1.3.0. The grant ceremony was the wrong fix; modal gates replaced it. Existing grant files on disk are ignored. |

## Human gates and auto-promote

| Surface | Status | Notes |
|---|---|---|
| Manifest gate (modal `AskUserQuestion`) | Stable | Drafted manifest reviewed in chat, decision via one-click modal. v1.3.0 retired the chat-`APPROVE` ceremony; case-insensitive residue test (`test_no_chat_approve_instructional_residue`) pins the migration. |
| Plan gate (modal `AskUserQuestion`) | Stable | Same shape as manifest gate. Decision options: APPROVE / REPLAN / View. |
| Manager gate (modal `AskUserQuestion`) | Stable | Final gate. Auto-fires PROMOTE when auto-promote's six conditions all pass; otherwise modal with APPROVE / BLOCK / REPLAN. |
| Auto-promote evidence-driven mode | Stable | Six conditions parsed from artifact stack: verifier-clean, critic-clean, drift-clean, policy-passed, judge-clean, tests-passed. Vacuous-pass for docs-only runs preserved from v1.3.1. |
| Directive-bound auto-approve (manifest + plan gates) | Stable | Conformant runs (`directive.yaml` matches manifest + scope-lock) skip the manifest and plan modals. PR #5 amendments all present: bind-after-conformance, append-not-prepend, exit-3 CONTRACT_DIVERGED on resume mismatch, downstream re-verify. Covered by `test_directive_contract` (14 tests). |

## Pipeline stages

| Stage | Status | Notes |
|---|---|---|
| `research` (researcher.md) | Stable | Fresh-context subagent, surfaces director decisions, produces `research.md`. |
| `plan` (planner.md) | Stable | Reads research + manifest, produces `plan.md`. |
| `test-write` (test-writer.md) | Stable | Feature pipeline only. Produces failing tests in `failing-tests-report.md`. |
| `execute` (executor.md, pre-edit fact-forcing) | Stable | Pre-edit fact-forcing gate enforced by drift-detector and critic verifying the fact block exists for every touched file. |
| `policy` (`scripts/policy/run_all.py`) | Stable | Aggregates check_allowed_paths, check_no_todos, check_adr_gate, check_manifest_schema, check_manifest_immutable, check_critic_evidence, check_manager_evidence, plus the v2.0 conditional checks (see below). |
| `verify` (verifier.md) | Stable | Independent fresh-context check against manifest exit criteria. |
| `drift-detect` (drift-detector.md) | Stable | Compares manifest contract vs assembled final state. Emits the `**Drift:**` count line auto-promote parses. |
| `critic` (critic.md, six lenses) | Stable | Hostile cold read across engineering / UX / tests / docs / QA / scope. Emits `**Findings:**` count line. |
| `auto-promote` (`scripts/auto_promote.py`) | Stable | Six-condition check + directive `acceptance.manager` extension. |
| `manager` (manager.md) | Stable | Cites verifier verbatim; can be auto-fired by auto-promote. |

## Pipelines

| Pipeline | Status | Notes |
|---|---|---|
| `feature.yaml` — full stage sequence for new functionality | Stable | Default pipeline. Eight stages, three human gates, automated policy gate. |
| `bugfix.yaml` — reproduce → patch | Stable | Collapses test-write + execute; same gate structure as feature. |
| `module-release.yaml` — six-phase release pipeline | Beta | Phase 0 preflight + Phase 2 local rehearsal preserved from v0.2. Exercised on CivicSuite module releases; documented in `docs/module-release-handbook.md`; not covered by an automated end-to-end test. |
| Judge layer (opt-in via `.pipelines/action-classification.yaml`) | Beta | v0.4 surface, preserved through v2.0. Inner classify → judge → execute loop in executor. Opt-in only; standard runs do not produce `judge-log.yaml`. |

## v2.0 enforcement layers

| Surface | Status | Notes |
|---|---|---|
| Eleven-event Cowork lifecycle hook layer (`hooks/hook_runner.py`) | Stable | SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, PostToolUse, PostToolUseFailure, PreCompact, PostCompact, SubagentStop, Stop, SessionEnd. Wired through `hooks/hooks.json`. Covered by `tests/test_hooks.py` (47 tests including Pass 9/10/12 regressions). |
| Persistent file-backed run memory (Layer A) | Stable | `.agent-runs/<run-id>/memory/*.jsonl` written unconditionally. `handoff_current.md` re-injected on SessionStart and PostCompact — pipeline state survives context compaction. |
| Directive contracts (`directive.yaml` + SHA-256 binding) | Stable | bind-after-conformance, exit-3 CONTRACT_DIVERGED, append-not-prepend, downstream re-verify. Eleven tests in `test_directive_contract.py`. |
| Scope-lock authority (`check_scope_lock.py`, `check_rung_file_ownership.py`, `check_release_docs_consistency.py`) | Stable | Blocks work that drifts off the canonical release-plan rung. Covered by `test_scope_lock.py`. |
| DoD readiness gate (`check_execute_readiness.py`) | Stable | Blocks policy/verify until executor declares full Definition-of-Done with a parseable zero-blocker checklist. |
| Decision-ledger NDJSON schema-v1 (`check_decision_ledger.py`) | Stable | Validates `decision-ledger.ndjson` rows. Covered by `test_decision_ledger.py`. |
| Pipeline control-loop scripts (`check_pipeline_control_loop.py`, `stop_validator.py`, `final_response_gate.py`, `pipeline_continue.py`, `agent_decision_gate.py`) | Beta | Ten smoke tests added in audit Pass 13 (`test_control_loop_smoke.py`). The CHANGELOG explicitly calls this a "floor" — comprehensive coverage is a future sprint. |
| Manifest schema validation (`check_manifest_schema.py`) | Stable | Minimum-length `goal` and `definition_of_done`, non-empty `expected_outputs` / `non_goals` / `rollback_plan`, forbidden status words banned. Runs at Phase A2 and in the policy stage. |
| Manifest immutability (`check_manifest_immutable.py`) | Stable | Mid-run mutation of `manifest.yaml` fails the policy stage. |
| Intake bridge model (`active_run: drafting`) | Beta | Shipped in audit Pass 12. Hook layer surfaces drafting runs; scope-deny downgrades to advisory; absolute deny reasons (destructive command, credential exposure) still fire. Seven regression tests cover the bridge contract; broader project-shape variance not yet exercised. |

## Memory layer

| Surface | Status | Notes |
|---|---|---|
| Layer A — file-backed run memory (`.agent-runs/<run-id>/memory/*.jsonl`) | Stable | Unconditional. No network, no Docker, no SDK. Hooks always write here. Per-event redaction via `_redact_message_for_layer_a()` (Pass 9). |
| Layer A → Layer B forwarder (`memory/sync.py::flush_layer_a_to_mem0`) | Beta | Records with `metadata.type` in the closed taxonomy `{decision, task_learning, anti_pattern, user_preference, environmental, convention, session_state}` get forwarded; default-type mapping for all eleven hook events shipped in Pass 9. Idempotent per record fingerprint (sha256 in `.mem0/synced-hashes.txt`). |
| Layer B — Mem0 OSS (Qdrant + Postgres via `vendor/mem0/server/`) | Beta | Docker compose stack starts via `/agent-pipeline-claude:mem0 up`. Default `oss.base_url` fixed to `:8888` (API) in Pass 1; pre-fix configs silently 404'd against the dashboard port `:3000`. # TODO: confirm tier — `vendor/VENDOR_PINS.md` pins mem0 to "latest `main`" rather than a fixed commit, which is a supply-chain smell worth a separate decision. |
| Layer B — Mem0 Platform (managed) | Beta | Requires `MEM0_API_KEY` in env AND `.mem0/consent.json` with `grant: true` (FR-14). Without consent, writes fall through to local outbox at `.mem0/outbox/`. The consent gate is exercised in tests; an end-to-end run against the real Platform service is not. |
| `PolicyLayer` enforcement (FR-6 scoping, FR-7 taxonomy, FR-9 budget, FR-10 latency, FR-11 redaction, FR-13 circuit breaker, FR-14 consent) | Stable | 42 tests in `test_memory_layer.py` cover every FR. Fail-closed on malformed redaction regex (loses memory, never leaks secret). |
| Cross-session retrieval (week-2 session recalls week-1 decisions) | Experimental | Code path wired. Whether the agent actually surfaces useful prior decisions depends on (a) consistent `pipeline mem0 sync` usage, (b) records being tagged with the right `metadata.type`, (c) the Mem0 backend's own retrieval quality on this corpus. No claim of accuracy; this is the layer that "accumulates value only with usage." |
| `--cross-repo` opt-in search | Experimental | Code path wired through `PolicyLayer`. Useful only when multiple repos use the same Mem0 backend with the same `user_id`. |
| Mem0 prune lifecycle (FR-12 aging) | Experimental | `/agent-pipeline-claude:mem0 prune` is dry-run by default; `--execute` requires interactive confirmation. Aging policy code is present; a real long-running install is needed to exercise the policy in practice. |
| Layer B circuit breaker (FR-13: 5 consecutive failures → 5-min open → local outbox) | Stable | Exercised in `test_memory_layer.py`. The outbox path is the load-bearing fallback when Layer B is unreachable; outbox replay on recovery is part of `sync`. |

## Test infrastructure

| Surface | Status | Notes |
|---|---|---|
| Unit + integration tests (`tests/`, 289 passing) | Stable | Runs from clean clone via `pytest -q`. No network, no Docker, no API key needed for the 289 passing tests. |
| Cleanroom plugin-load test (`test_cleanroom_install.py`) | Stable | Three tests: loads-via-plugin-dir, validates, structure-check. No API key needed. |
| Cleanroom smoke test (`test_cleanroom_smoke.py`) | Beta | Requires `ANTHROPIC_API_KEY`. Skipped in default CI. Exercises real-model invocation through the scaffolded skill loader. |
| Codex forward-compatibility (`test_codex_forward_compat.py`) | Stable | Pins the `directive_utils.py` and `hook_utils.py` shapes against the codex v0.9.0 fixture so the cross-runtime memory record schema can't drift. |
| Pipeline-payload mirror lockstep (15+ checks) | Stable | Top-level scripts under `scripts/` must match the mirrors under `skills/pipeline-init/references/pipeline-payload/scripts/`. Pass 2 centralized `find_repo_root`; Pass 8a closed the same-class mirror drift. Regression tests pin both directions. |

## Known broken / disabled

- **`hooks/hooks.json` invokes `python` (not `python3`).** On macOS and many Linux distros the binary is `python3` and `python` is unset; the hook layer silently no-ops there. Documented as QA-012 in the v2.0.0 audit; USER-MANUAL ships a launcher-shim workaround (`ln -s "$(which python3)" ~/.local/bin/python` or `apt install python-is-python3`). Long-term fix (platform-aware launcher) deferred to a follow-up.
- **`/agent-pipeline-claude:run-autonomous`** and **`/agent-pipeline-claude:grant-autonomous`** are no-op shims since v1.3.0. They will not be reactivated; the grant ceremony was the wrong fix.
- **`scripts/check_autonomous_mode.py`** and **`scripts/check_autonomous_compliance.py`** are no-op shims for v1.x backward compat. Pinned by `test_check_autonomous_mode_is_noop` and `test_check_autonomous_compliance_is_noop`.
- **Mem0 vendor pin tracks `latest main`** (see `vendor/VENDOR_PINS.md`). To refresh: edit `MEM0_VENDOR_PIN` in `scripts/mem0_bootstrap.py`. This is documented but is a known smell — pinning to a moving branch is not the same as a fixed commit. # TODO: confirm tier — should this be promoted to "Known broken" or stay a Beta caveat?
- **Single-model-family correlated blind spots.** Critic and verifier run in the same model family. If both share a wrong assumption that fits the manifest, both sign off and auto-promote fires green. This is the honest limit documented in `ARCHITECTURE.md §8`; dual-AI (v0.3 cross-family audit, scaffolded by `/agent-pipeline-claude:audit-init`) is the only structural defense.

## Reporting issues

If something documented as "Stable" or "Beta" doesn't work for you, please open an issue at https://github.com/scottconverse/agent-pipeline-claude/issues with:

- Plugin version (`cat .claude-plugin/plugin.json | jq -r .version`).
- Surface (Cowork or Claude Code CLI), OS, Python version.
- The full `python scripts/show_run_status.py --run <run-id>` output, or the failing test name + `pytest -q tests/<file>::<test>` output.
- The exact slash command or operator action that reproduced the problem.
- For hook-layer issues: contents of `.agent-runs/<run-id>/memory/memory_probe.log` and `.agent-runs/<run-id>/memory/events.jsonl` (redact secrets before posting).

For security issues (e.g., a redaction-layer bypass that leaks a secret into a durable memory artifact), please follow the disclosure path in `CONTRIBUTING.md` rather than filing a public issue.
