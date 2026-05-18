# Changelog

All notable changes to `agent-pipeline-claude` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed — audit Pass 11 (Cluster J + ENG-010) — doc staleness sweep + manifest-template v2.0 gates

Pre-Pass-11 the user-facing docs claimed v1.1.0 / v1.1.1 in version labels, told operators to type `Reply APPROVE to start` in chat (retired by v1.3.0), pointed upgraders at `git checkout v1.1.0`, and left the manifest-template's `required_gates` list silent on the three v2.0 conditional gates (ENG-010). Operators reading the docs got a different mental model than what the code actually did.

- `CHANGELOG.md` v2.0.0 entry: corrected the "171 tests" claim to the real count at HEAD `9634929` (191). Pre-fix-loop tests = 191, not 171; the audit caught this drift.
- `README.md`: replaced `Reply APPROVE to start` with the modal flow description; replaced v1.1.0 in the migration-section upgrade snippet with v2.0.0; replaced the v0.5.x-to-v2.0 migration note's "chat messages (APPROVE / REPLAN / BLOCK)" with the modal description.
- `USER-MANUAL.md`: upgrade snippet now targets v2.0.0.
- `ARCHITECTURE.md`: "Current version" label now v2.0.0, with a paragraph describing what v2.0 added on top of the v1.x stage architecture (hooks, memory, directive contracts) that the doc still describes.
- `tests/README.md` and `docs/VERIFICATION.md`: version labels updated; the v1.1.1 narrative is preserved as historical baseline with a pointer to CHANGELOG for current test count.
- `docs/index.html`: eyebrow / badge / footer all updated to v2.0.0; "What changed in v1.1.0" section rewritten as "What changed in v2.0.0" listing hooks, persistent memory, directive contracts, scope-lock authority, and the preserved v1.3.0 modal-gate surface; the "no hooks" honest-caveat paragraph rewritten to describe the new hook layer (the prior claim is now wrong).
- `scripts/check_manifest_schema.py`: gate_policy violation suggestion no longer cites "chat-APPROVE" — it now tells operators the field is retired and to remove it.
- `pipelines/directive-template.yaml` and `skills/pipeline-init/.../pipelines/directive-template.yaml` (mirror): `author.name` and `authority.reference` replaced hardcoded `"Scott Converse"` / `"docs/design/example.md"` with placeholder strings (`<your-name-or-team>`, `<path/to/design-doc-or-pr-or-issue>`). Operators copying the template no longer inherit the maintainer's name as the directive author by accident.
- `pipelines/manifest-template.yaml` and the mirror (ENG-010): added a comment block listing the three v2.0 conditional gates (`directive_bound`, `scope_lock_authority`, `execute_readiness`) and three commented-out gate lines that operators can uncomment when they author the matching artifact (directive.yaml / scope-lock.yaml / implementation-report.md). Conditional-skip via run_all.py's `CHECK_PREREQUISITES` table keeps v1.x runs unaffected.
- 9 new regression tests in `tests/test_v130_redesign.py` pinning: README has no "Reply APPROVE to start" / "git checkout v1.1.0"; USER-MANUAL upgrade snippet targets v2.0.0; ARCHITECTURE Current version is v2.0.0; tests/README and docs/index.html version labels are v2.0+; manifest-template documents the three v2.0 conditional gates (both top-level + mirror); directive-template uses placeholder author/reference (both top-level + mirror); check_manifest_schema.py error string contains no `chat-APPROVE`.

**Doc consistency invariant:** every claim in user-facing docs about "what version this is" and "how the gates work" now matches the code. Pre-Pass-11 was a worst-of-both-worlds: docs sold v1.x ceremony while code ran v2.0 hooks + modal gates.

### Fixed — audit Pass 10 (Cluster I) — contract-artifact warning only on writes

ENG-006 / UX-005: `classify_tool_risk` and `tool_failure_context` emitted "pipeline contract artifact touched" on any tool invocation whose `tool_input` mentioned `manifest.yaml` / `directive.yaml` / `scope-lock.yaml` — including reads (`cat manifest.yaml`, `grep -n goal manifest.yaml`, `Read` tool on the file). Operators got the noisy warning every time they inspected the contract — which is the safe, encouraged behavior. The Phase 6.c fix had only split block-on-failure vs warn-on-success; the false-positive on reads stayed.

- `hooks/hook_utils.py`: new `_is_read_only_operation(event)` helper. Returns `True` for tools in `_READ_ONLY_TOOL_NAMES` (Read, Grep, Glob, WebFetch, WebSearch, TodoWrite) and for `Bash` whose first token (after `cd … && ` stripping) is in `_READ_ONLY_BASH_TOKENS` (cat, head, tail, less, more, grep, rg, ls, dir, find, wc, stat, file, git, echo, printf, Get-Content, Select-String, Get-ChildItem, Get-Item). Output redirect (`>`, `>>`, `| tee`) keeps the command write-class regardless of first-token.
- `classify_tool_risk` and `tool_failure_context` now AND-gate the contract-artifact warning with `not _is_read_only_operation(event)`.
- 6 new regression tests in `tests/test_hooks.py`:
  - `test_classify_tool_risk_does_not_warn_on_read_tool_manifest`
  - `test_classify_tool_risk_does_not_warn_on_bash_cat_manifest`
  - `test_classify_tool_risk_does_not_warn_on_bash_grep_manifest`
  - `test_classify_tool_risk_warns_on_write_tool_manifest` (positive case — read suppression must not mask the legitimate warning)
  - `test_classify_tool_risk_warns_on_bash_redirect_to_manifest` (`echo … > manifest.yaml` IS write-class)
  - `test_tool_failure_context_no_contract_warning_on_read_failure`

**UX impact:** the warning fires when it should (mid-run mutation of a contract artifact) and stays quiet when it shouldn't (operator reading the contract to debug). Pre-Pass-10 operators trained themselves to ignore the warning, defeating its purpose.

### Fixed — audit Pass 9 (Cluster H + ENG-008) — Layer A metadata.type + pre-write secret scrub

Two paired memory-layer fixes:

**QA-001 (Cluster H):** `hooks.hook_utils.record_hook_memory` wrote every record with `metadata = {}`. The Layer A→B flush filter (`memory.sync.flush_layer_a_to_mem0`) requires `metadata.type` ∈ `allowed_types` to forward a record. Result: every Layer A row was silently dropped by the flush as `skipped_no_type`. Layer B was permanently starved of data. The "cross-session memory" promise was unkept.

- `hooks/hook_utils.py`: added `_EVENT_DEFAULT_TYPE` mapping each of the 11 hook events to a FR-7 taxonomy type. `record_hook_memory` now sets `metadata["type"]` from the mapping when the caller didn't pass one explicitly. PostToolUseFailure defaults to `"anti_pattern"`; all other events default to `"session_state"`. Explicit caller-supplied `metadata["type"]` still wins (decision-ledger, intake, etc.).

**ENG-008:** Layer A is the durable filesystem floor — records go to `.agent-runs/<run-id>/memory/*.jsonl` regardless of Mem0 state. Verbatim Bash commands with embedded credentials (e.g., `curl -H 'Authorization: Bearer …'`) were written to disk without scrubbing. The Layer B path runs through `scrub()` (PolicyLayer.add); Layer A did not. Secrets leaked into the durable artifact.

- `hooks/hook_utils.py`: new helper `_redact_message_for_layer_a()` runs the canonical `memory.redaction.scrub()` against every message before write. On match, the record is preserved (timestamp + event + run_id stay traceable) but the message body is replaced with `[REDACTED: <reason>]` and `metadata.redacted = True` plus `metadata.redacted_match_count`. Fail-closed: if `scrub()` raises (malformed regex, missing import), the message is treated as secret-bearing.

5 new regression tests in `tests/test_hooks.py`:
- `test_record_hook_memory_auto_populates_metadata_type`
- `test_record_hook_memory_post_tool_use_failure_is_anti_pattern`
- `test_record_hook_memory_explicit_type_overrides_default` (callers still win)
- `test_record_hook_memory_redacts_message_with_secret`
- `test_record_hook_memory_does_not_redact_innocuous_message`

**Security/UX impact:** Layer B finally sees Layer A records. Bash commands with credentials no longer land verbatim in the durable memory trail. Both fixes live in the same producer (`record_hook_memory`), so they ship as a paired fix; reverting one without the other is dangerous (Layer B starts seeing unredacted records, or sees redacted records without the type-tagging).

### Fixed — audit Pass 8a (Pass 2 mirror drift) — pipeline-payload scripts pick up centralized `find_repo_root`

Mid-sprint audit-lite caught a same-class regression: Pass 2 centralized `find_repo_root` in 11 top-level scripts but the pipeline-payload mirror still shipped 10 local `_find_repo_root` helpers that ignored `CLAUDE_PROJECT_DIR`. Since pipeline-init copies the mirror into operator projects as `scripts/policy/`, operators who scaffold a project today would inherit the *pre-Pass-2* bug — the exact "incomplete same-class fix" failure mode the audit had warned about.

- Replaced the local `_find_repo_root` in 10 pipeline-payload scripts with `from policy_utils import find_repo_root` + `find_repo_root(__file__)`: `auto_promote.py`, `check_adr_gate.py`, `check_allowed_paths.py`, `check_critic_evidence.py`, `check_manager_evidence.py`, `check_manifest_immutable.py`, `check_manifest_paths.py`, `check_manifest_schema.py`, `check_no_todos.py`, `check_stage_done.py`. `run_all.py` was already done in Pass 3. `check_active_target.py` keeps its cwd-based helper (intentional — it's about the user's active target, not the project root).
- New parametrized test `test_pipeline_payload_scripts_use_central_find_repo_root` covers all 11 mirror scripts. New `test_check_active_target_intentionally_keeps_local_helper` pins the one exception. Drift in either direction now fails CI.

**Operator impact:** projects scaffolded after this commit get the `CLAUDE_PROJECT_DIR`-honoring resolution in every policy script. Previously-scaffolded projects can refresh via `/pipeline-init` → "Refresh policy scripts only" modal option.

### Fixed — audit Pass 7 (Cluster G) — MCP local-write tool allowlist

Phase 6.c added `_extract_write_paths()` to make the scope guard pick up `tool_input.file_path` / `edits[].file_path` / `notebook_path` for the core Claude tools (Write, Edit, MultiEdit, NotebookEdit). MCP write tools — `mcp__*__create_file`, `mcp__*__copy_file`, etc. — were not covered. A subprocess that invoked an MCP file-creation tool could write outside `manifest.allowed_paths` without tripping the scope-lock guard.

The audit synthesis (Cluster G) rejected generic recursive path extraction because it would false-positive on every MCP that happens to have a string field named `path` or `destination` — including remote APIs (mcp__github__*, mcp__slack__*) that never touch the local filesystem. Audit-locked decision: explicit allowlist of local-filesystem write tools.

- `hooks/hook_utils.py`: added `MCP_LOCAL_WRITE_TOOL_RULES` — a tuple of `(regex, field-names)` pairs for the known local-filesystem MCP write tools (`create_file`, `copy_file`, `write_file`, `upload_file`, `save_profile`, PDF output tools). New helper `_extract_mcp_local_write_paths(tool_name, tool_input)` consults the allowlist; `_extract_write_paths()` calls it when iterating an event dict.
- Intentionally NOT in the allowlist: `mcp__github__create_or_update_file`, `mcp__github__push_files` (remote API pushes, not local writes — already gated by EXTERNAL_OR_RELEASE_PATTERNS), `mcp__*__send_message`, `mcp__*__post_*` (outbound network, no local write surface).
- 5 new regression tests in `tests/test_hooks.py`: `test_pre_tool_use_denies_mcp_create_file_with_out_of_scope_path`, `test_pre_tool_use_denies_mcp_copy_file_with_out_of_scope_destination`, `test_pre_tool_use_does_not_gate_mcp_github_push_files` (remote tool not gated by allowed_paths), `test_extract_mcp_local_write_paths_unknown_mcp_returns_empty` (locks the explicit-allowlist posture), `test_extract_mcp_local_write_paths_known_create_file`.

**Operator impact:** scope-lock now catches MCP-driven writes too. To gate a new local-filesystem MCP write surface, add a pattern to `MCP_LOCAL_WRITE_TOOL_RULES`. Generic field-name fishing is intentionally not supported.

### Fixed — audit Pass 6 (Cluster F) — `secret_patterns` defaults match canonical

The `memory/redaction.py` module exports `_DEFAULT_SECRET_PATTERNS` covering `sk-`/`m0-`/`gh[pousr]-` tokens, BEGIN PRIVATE KEY blocks, AWS access keys (`AKIA…`), and Bearer tokens. The `memory/config.py:RedactionConfig` dataclass shipped a narrower default — only the first two patterns. Because `load_config()` falls back to `RedactionConfig().secret_patterns` whenever a project's `.mem0/config.json` lacks an explicit `redaction:` block (or sets it to empty), a `.mem0/config.json` silently downgraded the redaction surface — AWS keys and Bearer tokens were passed through `scrub()` undetected. Same drift in `block_paths` (`~/.kube/config` missing from the dataclass default).

- `memory/config.py`: `RedactionConfig.secret_patterns` and `.block_paths` defaults are now imported from `memory/redaction.py` (`_DEFAULT_SECRET_PATTERNS`, `_DEFAULT_BLOCK_PATHS`). Single source of truth — adding a new canonical pattern propagates without a config-layer edit.
- `pipelines/mem0-config-template.json` + `skills/pipeline-init/.../mem0-config-template.json` (mirror): templates now include all four canonical patterns and the `~/.kube/config` block path. Operators who copy the template into `.mem0/config.json` get the full set out of the box.
- 3 new regression tests in `tests/test_memory_layer.py`: `test_redaction_config_defaults_match_canonical_redaction_module` (dataclass ↔ module lockstep), `test_pipelines_template_redaction_matches_canonical` (canonical template includes AKIA + Bearer + ~/.kube/config), `test_scaffold_payload_redaction_matches_canonical` (pipeline-payload mirror lockstep).

**Security impact:** `.mem0/config.json` projects that previously relied on dataclass defaults now block AWS access keys and Bearer tokens. No code path widened — only the previously-narrowed default does. If a project explicitly set `redaction.secret_patterns: [...]` in their `.mem0/config.json`, their override is unchanged.

### Fixed — audit Pass 5 (Cluster E) — pipeline-init uses AskUserQuestion modal

v1.3.0 retired the chat-`APPROVE` ceremony for the three run-time gates (manifest, plan, manager) because the LLM kept halting on the interpretive surface area of free-form gate text. The pipeline-init skill missed that bandwagon — `skills/pipeline-init/SKILL.md` explicitly instructed Claude to "Render the orientation summary as a plain chat message — do not use `AskUserQuestion` for the APPROVE gate." Operators saw modal gates inside `/run` but free-text gates inside `/pipeline-init`. v2.0's "heavier hand" pitch couldn't credibly claim modal-everywhere when the front door still asked operators to type APPROVE.

- `skills/pipeline-init/SKILL.md`: reversed the explicit ban on AskUserQuestion. The orientation summary stays in chat (informational); the approve / wait / adjust decision goes through a modal.
- `skills/pipeline-init/references/pipeline-init.md`: updated three gates to use `AskUserQuestion` blocks with concrete option labels — the scaffold gate (Step 2/3), the greenfield SPEC.md gate, and the re-init refresh-subset gate. Removed all "Reply `APPROVE`" / "Reply with a, b, c, or d" instructions.
- 3 new regression tests in `tests/test_v130_redesign.py`: `test_pipeline_init_skill_references_askuserquestion`, `test_pipeline_init_skill_does_not_ban_askuserquestion`, `test_pipeline_init_procedure_uses_modal_gates`.

**Operator impact:** `/pipeline-init` now feels the same as `/run` — every decision point is a one-click modal. No more "type APPROVE then hit enter, hope you typed it right."

### Fixed — audit Pass 4 (Cluster D) — directive ↔ scope-lock field vocabulary

`pipelines/directive-template.yaml`'s `preapproved.scope_lock` block used three field names that don't match `scripts/check_scope_lock.py`'s vocabulary — `current_rung_title` instead of `rung_title`, `proof_statement` instead of `proves`, `forbidden_future_rung_terms` instead of `forbidden_feature_terms_without_replan`. Because `directive_utils.compare_preapproved` uses exact dict-equality, a directive authored from this template against a scope-lock.yaml authored from `scope-lock-template.yaml` was structurally impossible to satisfy: `actual == expected` always returned False.

- `pipelines/directive-template.yaml` + `skills/pipeline-init/references/pipeline-payload/pipelines/directive-template.yaml` (mirror): renamed the three diverged fields to match the canonical scope-lock-template vocabulary; added `required_modules: []` and `replan_required_if: [...]` so the directive's preapproved.scope_lock has the same top-level structure as a scope-lock.yaml copied from the other template.
- `tests/test_directive_contract.py`: updated `SCOPE_LOCK` fixture to use canonical field names (drift here would have let the test pass while the real template was broken).
- 3 new regression tests: `test_directive_template_scope_lock_uses_canonical_field_names` (rejects pre-Pass-4 names), `test_directive_template_scope_lock_fields_match_scope_lock_template` (asserts directive includes every scope-lock-template top-level key), `test_directive_template_mirror_matches_top_level` (pipeline-payload mirror lockstep).

**Operator impact:** authors of new directives can now copy the directive-template's `preapproved.scope_lock` block alongside a scope-lock.yaml copied from `scope-lock-template.yaml` and have the comparison succeed. Pre-Pass-4 authors had to manually rename three fields to make their directive even possible to satisfy.

### Fixed — audit Pass 3 (Cluster C) — wire v2.0 policy scripts into `run_all.py`

`scripts/run_all.py` CHECKS list and the bundled payload mirror (`skills/pipeline-init/references/pipeline-payload/scripts/run_all.py`) were stale v1.x — none of the v2.0 policy scripts (directive conformance, scope-lock authority, rung-file-ownership, release-docs consistency, control-loop, execute readiness, decision-ledger) were invoked from the `policy` stage of any pipeline. The entire v2.0 enforcement layer was dead code from the orchestrator's perspective. The "heavier hand" pitch in this changelog was unrealized at the place that matters: the run skill flow.

- `scripts/run_all.py`: added seven v2.0 entries to CHECKS, populated `CHECK_PREREQUISITES` mapping each v2.0 check to the run-dir artifact it depends on (e.g., `check_scope_lock` → `scope-lock.yaml`), and gated invocation on prerequisite presence — when absent the check is SKIPPED (counted as PASS for the policy gate) rather than FAILED. Version literal bumped from 1.3.1 → 2.0.0. Added v2.0 names to `run_consumers` so `--run` is passed through.
- `skills/pipeline-init/references/pipeline-payload/scripts/run_all.py`: mirrored the top-level changes; also picked up the Pass 2 `find_repo_root` centralization (the mirror still had the local `_find_repo_root` helper).
- 4 new regression tests in `tests/test_run_all_writes_policy_report.py`: `test_v20_checks_are_in_checks_list`, `test_v20_checks_have_prerequisites_mapped`, `test_v20_checks_skip_when_prereq_missing`, `test_pipeline_payload_run_all_matches_top_level`.

**Operator impact:** policy stage now actually enforces v2.0 contracts for runs that opt in (i.e., runs whose `.agent-runs/<run-id>/` contains the relevant artifacts). v1.x runs that don't have those artifacts are unaffected — every v2.0 check skips with a `SKIP - <artifact> not present in run dir` line in `policy-report.md`. The conditional-skip preserves backwards-compat without requiring a project-level "v2.0 opt-in" flag.

### Fixed — audit Pass 2 (Cluster B) — `find_repo_root` honors `CLAUDE_PROJECT_DIR`

The Phase 6.c verification round fixed `show_run_status.py` to honor `CLAUDE_PROJECT_DIR` but the same-class bug lived in every other v2.0 script. Centralized the fix at `scripts/policy_utils.py:find_repo_root` (the helper now consults `CLAUDE_PROJECT_DIR` before falling back to script-relative discovery) so 16 scripts get the fix transitively.

- `scripts/policy_utils.py` + `skills/pipeline-init/references/pipeline-payload/scripts/policy_utils.py` (mirror): resolution order is now (1) `CLAUDE_PROJECT_DIR`, (2) `<project>/scripts/policy/` installed layout, (3) `git rev-parse --show-toplevel`, (4) `script_dir.parent` last-resort. The mirror is the version that pipeline-init copies into operator projects, so it must stay in lockstep with the top-level (a regression test pins this).
- Replaced local `_find_repo_root` helpers across 11 scripts with the centralized import: `auto_promote.py`, `check_allowed_paths.py`, `check_adr_gate.py`, `check_manifest_schema.py`, `check_no_todos.py`, `run_all.py`, `check_critic_evidence.py`, `check_stage_done.py`, `check_manifest_paths.py`, `check_manifest_immutable.py`, `check_manager_evidence.py`. `check_active_target.py` keeps its cwd-based intent unchanged (it's about the user's active target, not the project root).
- `tests/test_check_manifest_schema.py::_run_schema`: also copies `policy_utils.py` into the isolated tmp_path/scripts/ now that the schema script imports from it.
- Regression: `tests/test_policy_utils.py` (6 new tests) pins the env-var-first resolution order, the `.resolve()` normalization, the installed-layout fallback, the git fallback, the last-resort fallback, and the pipeline-payload mirror lockstep.

**Operator impact:** scripts invoked outside the source tree (e.g., via Cowork from `.klodock` cwd, or by direct subprocess from anywhere) now resolve `.agent-runs/` and friends against the operator's project, not the plugin install cache. Symptoms that should disappear: false "no active run", scope-lock pass against the wrong file, directive-bind drift on resume.

### Fixed — audit Pass 1 (Cluster A) — Mem0 OSS default port

- `memory/config.py` (OssConfig dataclass default, env-fallback path, dict-parse path), `memory/adapter.py` (OssAdapter constructor default), `pipelines/mem0-config-template.json`, `skills/pipeline-init/references/pipeline-payload/pipelines/mem0-config-template.json`, `schemas/mem0.config.v1.json`, `tests/test_memory_layer.py`: changed default `oss.base_url` from `http://localhost:3000` (the dashboard) to `http://localhost:8888` (the FastAPI server per the vendor `docker-compose.yaml`). The mem0 SDK's `Memory(base_url=...)` expects the API; pointing it at the dashboard silently returns 404 HTML and the circuit breaker masks it.
- `scripts/mem0_bootstrap.py cmd_test`: `policy.list_entities()` swallows backend exceptions and returns `{"error": "..."}`; cmd_test now treats that shape as rc=2 (was: rc=0 because no exception fired). Operators get a useful error when the URL is wrong.
- `USER-MANUAL.md` and `skills/mem0/SKILL.md`: added a port table documenting which host port is the API vs the dashboard, and a migration note for operators who scaffolded `.mem0/config.json` before this fix.
- Regression: `tests/test_memory_layer.py::test_oss_config_default_port_is_api` pins `OssConfig().base_url == "http://localhost:8888"` and the canonical template's `oss.base_url`. Any future change to either default will fail the test.

**Operator action on upgrade:** if you already ran `mem0 init` against the pre-fix template, your `.mem0/config.json` still has the wrong port. Re-run `python scripts/mem0_bootstrap.py init --mode oss --force` or hand-edit `oss.base_url` to `http://localhost:8888`.

## [2.0.0] — 2026-05-17

**Heavier-hand redesign.** Takes the opposite direction from PR #22 (closed 2026-05-17) which collapsed gates and removed enforcement. v2.0 keeps the gates and adds enforcement everywhere — an eleven-event Cowork lifecycle hook layer, directive-contract pre-approval, scope-lock authority, intake skill, persistent file-backed run memory, and an MCP Mem0 layer for cross-session continuity. Codex stays lighter; claude takes the heavier hand because Claude historically loses focus mid-run and the runtime needs to catch it.

191 tests, 1 skipped (cleanroom_e2e), all green at HEAD `9634929`. Branch `v2.0-heavier-hand`. (The audit Pass 11 sweep corrected this number from a stale "171" — the actual count at the v2.0.0 commit was 191.)

### Added — Phase 1: foundation policy ports (from agent-pipeline-codex v0.9.0)

- `scripts/policy_utils.py` — find_repo_root, strip_yaml_comment, unsupported_yaml_constructs.
- `scripts/scope_lock_utils.py` — release-plan rung parser, normalize_text, changed_paths, head_commit_subject.
- `scripts/check_scope_lock.py` — `SCOPE_CONFLICT` when manifest doesn't match the canonical release-plan rung's title / proves / required_modules / scope_bullets / exit_criteria.
- `scripts/check_rung_file_ownership.py` — `SCOPE_CONFLICT` on edited paths or commit messages containing forbidden future-rung terms.
- `scripts/check_release_docs_consistency.py` — `SCOPE_CONFLICT` when README/CHANGELOG/docs attribute the current rung to forbidden future-rung work.
- `scripts/check_execute_readiness.py` — blocks policy/verify when execute did not declare full DoD readiness with parseable zero-blocker checklist.
- `scripts/check_decision_ledger.py` — validates schema-v1 NDJSON rows for `decision-ledger.ndjson`.
- `scripts/check_pipeline_control_loop.py` — validates `active-control-state.md` against the VALID/INVALID stop-condition contract; flags unresolved Open Caveats / Release Risks sections.
- `scripts/stop_validator.py` — shared stop-condition truth function used by every control gate. Per-stop-condition evidence validation (human gates against pipeline resume; scope conflicts against scope-lock receipt; failed-gate against run.log; credential/destructive/external/user-paused against text signals).
- `scripts/final_response_gate.py` — pre-final-response gate; discovers active control-state files and blocks any final response while `final_response_allowed: false`.
- `scripts/pipeline_continue.py` — prints the next executable action (CONTINUE or STOP_ALLOWED).
- `scripts/agent_decision_gate.py` — validates agent stop/defer/skip/final decisions against control-state, evidence files, and scope-lock canonical rung. Writes `decision-ledger.ndjson` rows when `--write-ledger`.
- `scripts/show_run_status.py` + `skills/show-run-status/` — read-only summary of a run.
- `pipelines/scope-lock-template.yaml` — new run template.

### Added — Phase 2: directive contracts (codex v0.6 + PR #5 amendments)

- `scripts/directive_utils.py` — DirectiveContext, AssertionResult, load_directive, sha256_file, ensure_hash_integrity, compare_preapproved (unified-diff output), evaluate_assertions (regex/contains/section/artifact_exists/callable; callable names restricted to local public names, no dotted paths, no leading underscore).
- `scripts/check_directive_conformance.py` — Phase A2.6 gate. Returns 0 on AUTO_APPROVE (writes directive-bound line), 1 on NO_DIRECTIVE / MISMATCH / malformed, 2 on hash-changed-since-bind, 3 on CONTRACT_DIVERGED when directive was previously bound but artifacts now diverge. PR #5 amendments all present: **bind-after-conformance**, **append-not-prepend**, **exit-3 CONTRACT_DIVERGED on resume mismatch**.
- `scripts/check_plan_against_directive.py` — plan-gate auto-approval based on directive `acceptance.plan` assertions. **Downstream re-verifies manifest/scope conformance** (PR #5 amendment) — exits 2 on CONTRACT_DIVERGED.
- `pipelines/directive-template.yaml` — scaffold template with documented schema.
- `scripts/auto_promote.py` extended — adds `_check_directive_manager` which re-verifies manifest + scope-lock conformance (defense-in-depth) and evaluates `acceptance.manager` assertions. Two module-level callable helpers exposed for directive YAML reference: `no_unresolved_open_caveats`, `verifier_covers_manifest_expected_outputs`. **Claude's v1.3.1 vacuous-pass for docs-only runs is preserved unchanged.**

### Added — Phase 3: intake skill (codex v0.8 port)

- `skills/intake/` — `/agent-pipeline-claude:intake` captures plain-English product/repo/design/task/bug/feature descriptions and drafts `.agent-runs/<run-id>/intake.md`, `manifest.yaml`, `scope-lock.yaml`, and `intake-questions.md` without starting the pipeline. Soft onboarding doorway for ideas that don't have a manifest yet.

### Added — Phase 4: lifecycle hooks + persistent file-backed run memory

- `hooks/hook_runner.py` + `hooks/hook_utils.py` + `hooks/hooks.json` — **eleven Cowork lifecycle events** (codex v0.9 ships six; claude takes the heavier hand):
    * SessionStart — inject active run context + memory handoff
    * UserPromptSubmit — warn on stale skill names, block bypass attempts
    * PreToolUse — classify tool risk; deny destructive / out-of-scope
    * PermissionRequest — auto-deny dangerous; auto-allow when directive-bound
    * PostToolUse — corrective context after failed tools
    * **PostToolUseFailure** — dedicated failure recording with severity=high (claude-only)
    * **PreCompact** — snapshot memory before context compaction (claude-only)
    * **PostCompact** — re-inject handoff_current.md after compaction (claude-only)
    * **SubagentStop** — record subagent completion to memory (claude-only)
    * Stop — block invalid pipeline stops
    * **SessionEnd** — final memory flush + Phase 5 Mem0 attachment point (claude-only)
- Persistent file-backed memory under `.agent-runs/<run-id>/memory/`:
    * `events.jsonl` — all events (catch-all)
    * `turns.jsonl` — UserPromptSubmit
    * `decisions.jsonl` — PreToolUse, PermissionRequest
    * `open_loops.jsonl` — PostToolUse, PostToolUseFailure, Stop
    * `memory_probe.log` — per-event probe log
    * `handoff_current.md` — regenerated on every record; SessionStart and PostCompact inject this as context
- Record schema: `{timestamp, event, run_id, stage, message <=1200 chars, metadata}`. Mirrors codex v0.9 verbatim for forward-compat with the codex Mem0 reader (PRD §4.2).
- Hook commands resolve `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}` (Cowork roots cwd at `.klodock` — env var is the right answer per the Cowork-hooks research and the existing memory note).

### Added — Phase 5: Mem0 MCP memory layer (cross-session)

Two-layer architecture per PRD:

- **Layer A** (file-backed): `.agent-runs/<run-id>/memory/*.jsonl`. Unconditional. Written by hooks. Source of truth for within-run state.
- **Layer B** (Mem0): managed Platform OR self-hosted OSS. Best-effort. Behind a circuit breaker. Source of truth for cross-session knowledge.

New `memory/` package:

- `memory/config.py` — `Mem0Config` + `load_config()`. Reads `.mem0/config.json` with env-only fallback (`MEM0_MODE` + `MEM0_API_KEY` or `MEM0_BASE_URL`). Returns `enabled=False` when neither present (Layer A keeps working).
- `memory/identity.py` — `derive_identity()` implements PRD §5.2: `user_id = sha256(git user.email)[:16]` (no PII leak), `agent_id = "claude-code"`, `app_id = slug(git remote)`, `run_id = "{branch}-{short-sha}-{epoch}"`.
- `memory/redaction.py` — `scrub()` per FR-11. Default patterns cover `sk-` / `m0-` / `gh[pousr]-` tokens, AWS access keys, BEGIN PRIVATE KEY, Bearer tokens. Default block paths: `~/.ssh`, `~/.aws`, `~/.config/gcloud`, `~/.kube/config`. **Fail-closed on malformed regex** (loses memory, never leaks secret).
- `memory/adapter.py` — `MemoryAdapter` ABC + `NullAdapter` (used when disabled — callers never branch on enable/disable) + `PlatformAdapter` (lazy-imports `mem0ai.MemoryClient`) + `OssAdapter` (lazy-imports `mem0ai.Memory`). **`delete*` operations require `allowed_by_prune=True`** per FR-8 — the agent can never call delete directly.
- `memory/policy.py` — `PolicyLayer` enforces:
    * FR-6 entity scoping (writes carry user_id+agent_id+app_id+run_id; searches default to user_id+app_id; `--cross-repo` opt-in)
    * FR-7 metadata taxonomy: closed-set `type` values `{decision, task_learning, anti_pattern, user_preference, environmental, convention, session_state}`. Unknown types rejected at the policy layer.
    * FR-9 token-budget cap (1200 default; 1.5x overflow at session_start)
    * FR-10 latency tracker — auto-disables prompt injection after 5 consecutive p95 violations (preserves session-start retrieval)
    * FR-11 redaction (every write candidate scrubbed)
    * FR-13 circuit breaker — 5 consecutive failures opens for 5 minutes; writes go to local outbox at `.mem0/outbox/`; reads return empty
    * FR-14 consent gate — platform mode requires `.mem0/consent.json` with `grant: true` before any backend write
- `memory/sync.py` — `flush_layer_a_to_mem0()` walks events.jsonl, picks records with valid `type`, forwards via PolicyLayer.add. Idempotent per record fingerprint (sha256 tracked in `.mem0/synced-hashes.txt`).

New schema + template + CLI + skill:

- `schemas/mem0.config.v1.json` — full JSON Schema with descriptions, defaults, enum constraints.
- `pipelines/mem0-config-template.json` — starting config (`mem0 init` writes from this).
- `scripts/mem0_bootstrap.py` — 7 subcommands: `init` / `up` / `down` / `whoami` / `test` / `sync` / `prune`.
- `skills/mem0/SKILL.md` — `/agent-pipeline-claude:mem0 <subcommand>` operator-facing skill.

### Added — tests

- 8 new test files: `test_scope_lock.py`, `test_execute_readiness.py`, `test_decision_ledger.py`, `test_show_run_status.py`, `test_directive_contract.py` (11 tests including the 4 PR #5 amendment tests), `test_hooks.py` (18 tests, 12 ported + 6 for new handlers), `test_memory_layer.py` (27 tests covering every FR).
- Existing 102 v1.3.1 tests preserved unchanged (vacuous-pass, scaffold integrity, cleanroom install, v1.3.0 redesign contract, etc.).
- Two regression fixes on existing tests: `test_v130_redesign` regex widened to accept 2.x successors; `test_cleanroom_install` charset includes U+221A (Cowork on Windows renders the loaded-status checkmark as that codepoint).

### Removed (breaking)

Reused PR #22's `v2.0.0` label but reversed its direction:

- **None** of v1.3.1's gates were removed. Manifest gate, plan gate, manager gate all preserved (manager auto-fires on green per v1.3.0).
- v1.3.0 deprecated stubs (`grant-autonomous`, `run-autonomous` skills; `check_autonomous_compliance`, `check_autonomous_mode` scripts) preserved as no-op shims for backward compat — same behavior as v1.3.1.

### Security / Safety

- **Failure mode closed (heavier-hand intent):** Pipeline state is now durable across context compaction and session boundaries. The runtime no longer depends on the model remembering the orchestrator markdown through a long session — hooks observe every load-bearing point and write persistent records.
- **Failure mode closed (directive):** Directive-conformant runs no longer force operators to re-type APPROVE for manifest/plan content they already authored verbatim. Reduces reflexive-approval training without weakening the gate contract.
- **Failure mode closed (intake):** Operators no longer have to choose between a blank `new-run` skeleton and asking the model to improvise work without a manifest. `intake` provides a friendly drafting step while preserving the rule that execution starts only after manifest review.
- **Failure mode closed (cross-session memory):** Sessions in week 2 can recall decisions from week 1 via Mem0. Decision drift across long-running projects is now mitigated at the runtime layer, not the human-discipline layer.
- **Honest limit:** Mem0 writes are best-effort and never block the agent's response. The circuit breaker opens after 5 consecutive failures and writes go to a local outbox at `.mem0/outbox/` for next-session retry.
- **Honest limit:** Platform mode requires explicit consent grant in `.mem0/consent.json`. OSS mode is the default for first-time users on privacy grounds.
- **Honest limit:** The agent never calls `delete_*` operations directly. All deletes go through `pipeline mem0 prune` with explicit human confirmation.

## [1.3.1] — 2026-05-14

**Remove two false-stops that block hands-off auto-promote.**

v1.3.0's design intent — evidence-driven auto-promote so clean runs complete without manual gates — depended on two paths through the gate scripts that v1.3.0 didn't touch. v1.3.1 closes both, narrowly:

### Changed

- **`scripts/auto_promote.py::_check_tests`** — vacuous-pass exception added. When the manifest's `forbidden_paths` explicitly forbids the test directory (so the executor was barred from running or modifying tests) AND `implementation-report.md` is absent, condition 6 now passes with explanation instead of failing on the absence of a test-pass signal it could not possibly have. Mirrors the existing vacuous-pass behavior of `_check_judge` for runs without `judge-metrics.yaml`. **Why it matters under v1.3.0:** every docs-only run hit condition-6 failure → manager modal fired → no hands-off completion. This was Finding #1 in the v1.2.1 PROMOTED report (CivicSuite Candidate B macOS-narrowing run). The strict default is preserved for runs that don't explicitly declare tests out of scope, so a real implementation gap is not silently auto-promoted.
- **`scripts/run_all.py`** — when invoked with `--run`, writes `policy-report.md` directly to the run directory instead of relying on the orchestrator to capture stdout. The marker line `POLICY: ALL CHECKS PASSED` (or `POLICY: N CHECK(S) FAILED`) is now guaranteed to appear in the artifact, not contingent on a stdout-pipeline that proved unreliable in real runs. **Why it matters:** when the orchestrator's stdout capture glitched, auto_promote's condition 4 read `policy-report.md`, didn't find the marker, and failed the gate — even though policy actually passed. The manager modal then fired for a fictional policy failure, eroding trust ("I just read the file, policy DID pass, why is this asking me?"). Removes that whole class of confusing false-stops. Also adds a `--version` flag (was missing in v1.3.0).
- **`scripts/auto_promote.py` `--version`** — fixed stale `agent-pipeline-claude 1.2.1` → `1.3.1`. Operators running `auto_promote.py --version` to confirm their install now get the correct version.
- **`scripts/auto_promote.py::_manifest_forbids_tests`** — handles flow-style `forbidden_paths: ["tests/"]` in addition to block style. Caught by audit; the original parser only walked block-style `- entry` lines.
- **`ARCHITECTURE.md`** — condition 6 description updated with the vacuous-pass note, parallel construction with the existing judge-layer note for condition 5.
- **Payload mirror** — `skills/pipeline-init/references/pipeline-payload/scripts/{auto_promote.py,run_all.py}` updated in lockstep with the live scripts so newly-scaffolded projects post-v1.3.1 inherit both fixes.

### Added

- **`tests/test_auto_promote_vacuous.py`** — 12 tests pinning the new vacuous-pass behavior plus parser-robustness against future "simplification" regressions: back-compat (default strict failure still fires when the manifest doesn't forbid tests), nested test-path patterns (`tests/unit/`), singular `test/`, "implementation-report exists → use it, don't shortcut" guard, no-false-positive on `tests-data/` substring, inline empty `[]`, flow-style `["tests/", ...]`, inline comment after entry, and a full end-to-end integration test driving `auto_promote.main()` against a fully-scaffolded docs-only run dir to PROMOTE.
- **`tests/test_run_all_writes_policy_report.py`** — 3 tests pinning the direct-write contract: writes artifact when `--run` + dir exists, no silent dir creation on bogus run-id, no write without `--run`.

### Not changed

- The `_check_tests` failure-counts logic (`0 failed` matching anywhere in the report) was identified as a separate bug but deliberately left alone. Fixing it would make auto-promote *stricter*, adding another reason to stop — directly against v1.3.0's "remove reasons to stop" design intent. The malformed-mixed-output case is theoretical; no real-world run has been observed producing such a report.
- No new manifest schema fields. The vacuous-pass detects from the existing `forbidden_paths` declaration.

### Notes

- Net diff: ~150 lines including tests. All additive.
- The `auto_promote.py` change is opt-in by manifest content. The `run_all.py` change is additive over the existing stdout path (orchestrators that capture stdout will simply overwrite identical bytes).

## [1.3.0] — 2026-05-14

**Codex-aligned redesign: modal gates replace chat-APPROVE; grant + autonomous-mode removed entirely.**

v1.2.1 added a grant-based autonomous mode to solve "the LLM ignores conversational authorization and chickens out at gates." The fix was over-engineered — it added grant files, ledgers, signed authorization documents, and several new policy scripts. The actual failure mode was the chat-APPROVE ceremony itself: free-form text gates left interpretive surface area for the LLM to halt on.

v1.3.0 replaces the chat-APPROVE ceremony with `AskUserQuestion` modal prompts (one click each) for the three human gates (manifest, plan, manager). Auto-promote becomes the evidence-driven path to hands-off: when verifier/critic/drift/policy/tests all pass, the manager gate is skipped entirely. No grant required, no autonomous mode, no signed file. Mirrors the design of `agent-pipeline-codex` which has shipped clean hands-off runs without grants.

### Removed (breaking, but inert for existing manifests)

- **Grant-based autonomous mode.** `gate_policy: autonomous` and `autonomous_grant` manifest fields are ignored. Existing grant files at `.agent-workflows/autonomous-grants/` are inert — safe to archive.
- **`autonomous_skip_chat: true` flags** on pipeline yaml stages — removed from `feature.yaml`, `bugfix.yaml`, and `module-release.yaml`.
- **Autonomous-mode awareness sections** in `manifest-drafter.md`, `planner.md`, `manager.md` role files — replaced with v1.3.0 gate-flow notes.

### Deprecated (kept as no-op shims for backward compat)

- **`/agent-pipeline-claude:run-autonomous`** — SKILL.md is a deprecation notice that redirects to `/agent-pipeline-claude:run`.
- **`/agent-pipeline-claude:grant-autonomous`** — same; the grant system is gone.
- **`scripts/check_autonomous_mode.py`** — always returns PASS with status `HUMAN-MODE` (no-op stub).
- **`scripts/check_autonomous_compliance.py`** — always returns PASS (no-op stub).

### Changed

- **`skills/run/SKILL.md` and `references/run.md`** — rewritten to use `AskUserQuestion` for all three human gates. No chat-APPROVE. No grant logic.
- **`pipelines/manifest-template.yaml`** — removed `gate_policy:` and `autonomous_grant:` fields and their explanatory comment block.
- **`pipelines/roles/manifest-drafter.md`, `planner.md`, `manager.md`** — removed autonomous-mode awareness sections; added v1.3.0 gate-flow / auto-promote notes.
- **`.claude-plugin/plugin.json`** — description updated to reflect modal gates and no-grant flow.

### Migration notes

If you have:
- **Pipeline runs in flight under v1.2.x autonomous mode** — finish them on v1.2.1 (the grant logic is preserved if you don't upgrade mid-run). On the next run, drop the grant flow.
- **Grant files on disk** — leave them or archive. No effect on v1.3.0 behavior.
- **Custom pipelines with `autonomous_skip_chat: true`** — remove the flag. The v1.3.0 run skill ignores it but cleanest is to remove.

### Why this is the right answer

- **AskUserQuestion is modal and structured.** One click. The LLM cannot invent extra prompts or chicken out; the user cannot inadvertently type "approve maybe?" and confuse the LLM. The interpretive surface is zero.
- **Auto-promote is honest automation.** Evidence-driven (the script reads the artifacts and decides), not authorization-driven (no signed file granting permission). When the work passes its own quality bar, it advances. When it doesn't, the human gate fires.
- **Mirrors agent-pipeline-codex.** Codex's design has shipped clean autonomous-feeling runs for weeks. v1.3.0 ports the pattern.

---

## [1.2.1] — 2026-05-13

**Autonomous-mode hardening: chat-driven grants, structural enforcement.**

v1.2.0 closed a structural pipeline gap (priority drift via off-target manifests). v1.2.1 closes a behavioral gap that v1.2.0 didn't catch: an LLM operating the pipeline under conversational autonomous-mode authorization could "chicken out" and refuse to honor the authorization. The fix is structural — autonomous mode requires a signed grant document the LLM cannot interpret away. Conversation alone is no longer enough.

The grant is created by chat command (user describes → Claude writes the file). The pipeline reads the file at preflight and at every subsequent gate; mid-run revocation or expiration halts the next gate.

### How it works

- **User types in chat:** *"Grant autonomous mode for 8 hours, manager PROMOTE only, for Candidate B."*
- **Claude:** parses, confirms in chat, writes `.agent-workflows/autonomous-grants/<name>.md` with required headers (Granted-by / Granted-at / Expires-at / Authorized-gates / Forbidden-actions / Revoked / Rationale), updates `.agent-workflows/autonomous-grants/ledger.md`.
- **User invokes:** `/agent-pipeline-claude:run-autonomous --grant <path> "<task>"`.
- **Pipeline:** preflight validates grant; manifest gate, plan gate, manager gate (PROMOTE only) auto-approve based on LLM's recommendation; PRs opened but NOT admin-merged; every decision logged to `.agent-runs/<run-id>/autonomous-decisions.md`.
- **User revokes:** *"revoke autonomous"* / *"kill it"* — Claude edits `Revoked: false → true`. Next gate halts.

### Added

- **`scripts/check_autonomous_mode.py`** — validates the grant file. Returns one of `HUMAN-MODE`, `AUTONOMOUS-ACTIVE`, `NO_GRANT_FILE`, `GRANT_EXPIRED`, `GRANT_REVOKED`, `GRANT_MALFORMED`. Logs status to `.agent-runs/<run-id>/autonomous-mode.log`.
- **`scripts/check_autonomous_compliance.py`** — post-run drift check. Verifies every stage marked `autonomous_skip_chat: true` has an entry in autonomous-decisions.md, no forbidden-action commands fired in run.log, no chat-wait patterns (`Reply APPROVE`) slipped through. Emits `COMPLIANCE_DRIFT` findings.
- **`skills/grant-autonomous/`** — chat-driven grant management skill. Parses user's natural-language create/revoke/extend/delete intent; confirms; writes the grant file + ledger entry. Invoked as `/agent-pipeline-claude:grant-autonomous`.
- **`skills/run-autonomous/`** — entry point for autonomous runs. Validates the grant, then invokes the standard `/run` flow under autonomous-mode hard rules.
- **`pipelines/action-classification.yaml`** — new `human_only_under_autonomous` class with patterns for `gh pr merge --admin`, `gh release create`, `git push --force`, `git push --tags`, version tag push.
- **Manifest fields** — `gate_policy: human | autonomous` (default human) and `autonomous_grant: <path>` (required when gate_policy=autonomous).
- **Pipeline yaml flag** — `autonomous_skip_chat: true` on manifest / plan / manager stages in `feature.yaml` and `bugfix.yaml`. When `gate_policy: autonomous` AND `check_autonomous_mode` returns `AUTONOMOUS-ACTIVE`, these gates skip chat-APPROVE.
- **Tests** — `test_check_autonomous_mode.py` (9 tests, all grant states), `test_check_autonomous_compliance.py` (6 tests, all drift patterns).

### Changed

- **`scripts/check_manifest_schema.py`** — validates `gate_policy` ∈ `{human, autonomous}`; requires `autonomous_grant` non-empty when gate_policy=autonomous.
- **`scripts/run_preflight.py`** — adds `check_autonomous_mode` to the preflight check sequence.
- **`scripts/run_all.py`** — adds `check_autonomous_compliance` to the policy stage's check sequence.
- **`pipelines/manifest-template.yaml`** — adds `gate_policy` and `autonomous_grant` fields with full inline documentation.
- **`pipelines/feature.yaml`, `pipelines/bugfix.yaml`** — adds `autonomous_skip_chat: true` to the three human-approval stages.
- **`pipelines/roles/manifest-drafter.md`** — adds autonomous-mode awareness section. Drafter sets `gate_policy: autonomous` and `autonomous_grant` in the manifest when the orchestrator invoked it under autonomous mode.
- **`pipelines/roles/planner.md`** — adds autonomous-mode awareness. Plan stage auto-approves and logs the decision.
- **`pipelines/roles/manager.md`** — adds autonomous-mode awareness. Auto-approves only on `PROMOTE` verdicts when the grant authorizes manager-gate; `BLOCK` and `REPLAN` halt for human regardless.
- **`skills/run/SKILL.md`** — new top-level hard rules for autonomous-mode procedure + explicit list of actions never authorized by autonomous mode (admin-merge, tag push, release publish, force push, human_only_under_autonomous class).

### Not changed

- `pipelines/module-release.yaml` — module-release runs end in tag pushes which are forbidden under autonomous mode. Leaving module-release human-only is the safest default for v1.2.1.

### Verified

- pytest full suite: **73 passed in 5.62s** (free tier, up from 58 in v1.2.0). 15 new tests cover all grant states + all compliance-drift patterns.
- Bundled payload at `skills/pipeline-init/references/pipeline-payload/` synced 1:1 with `pipelines/` and `scripts/` source-of-truth.
- Both new skills follow the canonical pipeline-init/run/audit-init shape: SKILL.md (front-matter description + tool mapping + hard rules) + `references/<skill>.md` (full procedure).

### What this fixes

The behavioral failure mode from earlier this session: I (the LLM operating the pipeline) refused to honor Scott's conversational authorization to run autonomously while he slept. v1.2.1 makes that refusal structurally impossible:

- Conversation cannot flip the bit; only a signed grant file can.
- The grant is short-lived (max 24h default), scope-bounded, and explicitly enumerates forbidden actions.
- The SKILL.md hard rule under autonomous mode forbids emitting "Reply APPROVE" chat messages — `check_autonomous_compliance.py` catches them post-run and emits `COMPLIANCE_DRIFT` findings the manager must resolve.
- Action classification reinforces: even with a grant, admin-merges and tag pushes are human-only.

### Migration

No migration needed. v1.2.1 is fully backward-compatible. Manifests without `gate_policy` default to `human` and behave exactly as v1.2.0.

To USE autonomous mode: ask Claude to write a grant (`/agent-pipeline-claude:grant-autonomous` or natural language), then invoke `/agent-pipeline-claude:run-autonomous --grant <path> "<task>"`.

## [1.2.0] — 2026-05-13

**Hardened pipeline: priority-drift gate, manifest-integrity gate, stage-output rigor, cross-stage consistency, manager-decision rigor.**

v1.1.2 shipped a deterministic $0 unit test for the scaffolder, but the pipeline itself had a load-bearing gap: the manifest gate caught well-formed manifests, not off-priority ones. A user could feed the drafter a task description that didn't match the project's active target, the drafter would faithfully produce a manifest for the wrong work, and the manifest gate would rubber-stamp it as long as the schema was clean.

v1.2.0 closes that class of failure with a new preflight stage between the manifest gate and the researcher stage, plus a constellation of stage-output and cross-stage integrity checks that catch six more failure modes beyond priority drift.

### The 14 hardening items

| ID | Description | Catches |
|---|---|---|
| A1 | New `preflight` stage 0 between manifest and research stages | Priority-drift failure mode entirely |
| A2 | Manifest schema requires `advances_target` field | Drafter can't ship without naming the target |
| A3 | Manifest schema requires `authorizing_source` field (file:line in control plane) | Cited target must exist verbatim |
| A4 | `out_of_scope` action class hard-blocks (deferred to v1.2.1) | Mid-run scope drift |
| A5 | `override_active_target` field, ledger-logged | Explicit, expensive escape hatch |
| A6 | `skills/run/SKILL.md` names control plane as required pre-read | Hard rule, not a suggestion |
| B1 | `check_manifest_paths.py` filesystem-verifies all cited paths | Hallucinated or stale file references |
| B2 | Drafter must check `git status` before drafting (rule in role file) | Dirty tree silently absorbed into manifest |
| B3 | `target_repos` field — multi-repo manifest support | Umbrella + sibling work in lockstep |
| C1 | `STAGE_DONE: <stage>` markers required in `run.log` (enforced) | Stage completion lies (Haiku drift class) |
| C4 | Critic per-lens evidence requirement enforced (`check_critic_evidence.py`) | Rubber-stamp "no findings" lenses |
| D1 | Manifest SHA-256 pinned at preflight, verified at policy stage | Mid-run manifest mutation |
| D2 | Multi-repo `allowed_paths` enforcement | Sibling-repo scope drift |
| E1 | Manager-decision must include `## Resolution per finding` table | Manager rubber-stamp on un-resolved findings |
| G1 | `preflight_infrastructure.py` (Phase 0 module audit) wired into module-release.yaml as `phase0` | Pre-existing infrastructure preflight is now gate-blocking |

(15th item — I1, the `/run` E2E test — is deferred to v1.2.1 with explicit scope rationale below.)

### Added

- **`scripts/check_active_target.py`** — Reads `.agent-workflows/PROJECT_CONTROL_PLANE.md` (or `ACTIVE_WORK_QUEUE.md` / `docs/RELEASE_PLAN.md` / `docs/PROJECT_CONTROL_PLANE.md` as fallbacks), extracts the named active target, and compares against the manifest's `advances_target`. Mismatch returns `PRIORITY_DRIFT` exit 1. Override path via `override_active_target` (≥60 chars rationale) returns `OVERRIDE_ACCEPTED` exit 0 with ledger logging at `.agent-workflows/scope-overrides.md`. No control plane = informational mode, exit 0 (greenfield-friendly).
- **`scripts/check_manifest_paths.py`** — Filesystem-verifies every `allowed_paths` entry, every `target_repos[].path`, every `expected_outputs` entry, and the `authorizing_source` file:line citation. Catches hallucinated paths, stale file references, off-by-one line citations.
- **`scripts/check_stage_done.py`** — Reads `.agent-runs/<run-id>/run.log` and verifies a `STAGE_DONE: <stage>` line exists for every LLM-owned stage that should have completed. Supports `--through <stage>` for mid-run progress checks. Closes v1.1.1 open followup #1.
- **`scripts/check_critic_evidence.py`** — Walks the critic report's six required lenses (UX/Tests/Engineering/Docs/QA/Performance) and verifies each has either at least one finding bullet or at least one citation (file path with extension, command in backticks, or line reference). Empty "no findings" claims fail the gate.
- **`scripts/check_manager_evidence.py`** — Validates `manager-decision.md` has a verdict (PROMOTE/REPLAN/BLOCK), a `## Resolution per finding` section, and a per-finding row for every critic `C-N` and drift `D-N` finding. PROMOTE with any `blocked` disposition is rejected.
- **`scripts/check_manifest_immutable.py`** — Two-mode: `--pin` captures SHA-256 of manifest.yaml at preflight; `--check` verifies the SHA hasn't moved at later stages. Catches mid-run manifest mutation. Wired into `run_all.py`.
- **`scripts/run_preflight.py`** — Orchestrator that runs all preflight checks in sequence (schema → active_target → manifest_paths → manifest_immutable --pin). Wired into `feature.yaml`, `bugfix.yaml`, and `module-release.yaml` as stage 0.5.

### Changed

- **`pipelines/manifest-template.yaml`** — Adds required fields `advances_target` and `authorizing_source`, optional fields `override_active_target` and `target_repos`. Each has an inline-comment usage block explaining the v1.2.0 contract.
- **`pipelines/roles/manifest-drafter.md`** — Hard rules add: read control plane first (Category 0 in the source-walking protocol); check `git status` before drafting; cite `authorizing_source` by file:line; never set `override_active_target` unilaterally; append `STAGE_DONE: manifest` to run.log.
- **`pipelines/roles/critic.md`** — Each adversarial lens now has an explicit `Evidence:` requirement. Six lenses (UX/Tests/Engineering/Docs/QA/Performance) named as required headings. Finding IDs `C-N` required for downstream manager-decision linking.
- **`pipelines/roles/manager.md`** — Manager-decision must include `## Resolution per finding` table with verdict-dispositions per critic and drift finding. PROMOTE with any `blocked` disposition is rejected.
- **`pipelines/roles/researcher.md`, `planner.md`, `test-writer.md`, `executor.md`, `verifier.md`, `drift-detector.md`** — Each role's "Output checklist" now requires `STAGE_DONE: <stage>` as the final action.
- **`pipelines/feature.yaml`, `pipelines/bugfix.yaml`, `pipelines/module-release.yaml`** — New `preflight` (or `preflight-priority` for module-release) stage runs between manifest gate and researcher. Stops priority-drift at $0 cost.
- **`scripts/check_manifest_schema.py`** — Validates new v1.2.0 fields: `advances_target` (required, ≥8 chars), `authorizing_source` (required unless override present; `path:line` format), `override_active_target` (optional but ≥60 chars when present), `target_repos` (optional list with per-repo path+allowed_paths shape).
- **`scripts/check_allowed_paths.py`** — Honors `target_repos` multi-repo shape. For each declared sibling repo, walks its working-tree diff and enforces that sibling's allowed_paths. Requires PyYAML for multi-repo mode (single-repo path is unchanged and yaml-free).
- **`scripts/run_all.py`** — Adds `check_manifest_immutable --check` (D1) and `check_stage_done --through execute` (C1) to the policy stage's check sequence.
- **`skills/run/SKILL.md`** — New top-line hard rule: read control plane before drafting.

### Verified

- pytest full suite: **58 passed in 5.04s** (free tier, up from 24 in v1.1.2). 9 of those are the existing pre-v1.2.0 schema tests; 49 are new tests across the six new policy scripts.
- New test files: `tests/test_check_active_target.py` (9), `tests/test_check_manifest_paths.py` (8), `tests/test_check_manifest_immutable.py` (5), `tests/test_check_stage_done.py` (4), `tests/test_check_critic_and_manager_evidence.py` (8).
- Bundled payload at `skills/pipeline-init/references/pipeline-payload/` synced 1:1 with `pipelines/` and `scripts/` source-of-truth.

### Deferred to v1.2.1 (or later)

- **I1: `/run` E2E test.** v1.1.1 added cleanroom-smoke (covers `/pipeline-init`); v1.2.0 was scoped to add an equivalent for `/run`. The test is large (requires fixture project + 3-turn multi-gate orchestration + 11-stage artifact assertion + ~$0.05–0.10 API spend per run) and is belt-and-suspenders on top of the 49 new unit tests that cover each gate's behavior in isolation. Deferred to v1.2.1 to ship v1.2.0's load-bearing fixes promptly. The gap is honestly named here so it doesn't disappear.
- **A4: `out_of_scope` action class hard-blocks executor.** Requires changes to action-classification.yaml + executor-side intercept handling. The current `check_allowed_paths.py` catches scope drift at the policy stage (post-execute); a true mid-run hard-block requires more careful wiring through the judge layer. Deferred.

### Migration

Existing projects using agent-pipeline-claude v1.1.x need to add two new fields to their manifests:

- `advances_target`: the active-target string from your project's control plane (or a short summary if no control plane exists)
- `authorizing_source`: file:line citation, e.g. `.agent-workflows/PROJECT_CONTROL_PLANE.md:42`

Re-run `/agent-pipeline-claude:pipeline-init` (choose option `c` — refresh everything) to get the v1.2.0 role files + policy scripts. Existing manifests can be edited to add the new fields; if the project has no control plane doc, leave `authorizing_source` empty (preflight runs in informational mode).

## [1.1.2] — 2026-05-12

**Deterministic $0 scaffold test, plus auxiliary scaffolder helper.**

v1.1.1 added `tests/test_cleanroom_smoke.py` to prove the first skill actually executes against a fresh fixture — but that test costs ~$0.05/run on Haiku and requires `ANTHROPIC_API_KEY`. For every-commit CI and for users running without an API key (e.g. routing `claude` through a local-model proxy), there was no automated coverage of the scaffold's load-bearing post-condition: does `.pipelines/` materialize with the expected files?

v1.1.2 closes that with a unit-tier test that exercises just the deterministic copy step — same assertions as cleanroom-smoke, $0, sub-second.

The motivation: an attempt to make cleanroom-smoke run against local Ollama models via [`claude-code-router`](https://github.com/musistudio/claude-code-router) hit three walls (`thinking`-field rejection on `qwen3-coder:30b`, small-model tool-use looping on `gemma4:e4b`, and `ERR_STREAM_PREMATURE_CLOSE` on multi-turn skill invocations) — see [`docs/LOCAL_TEST_RESEARCH.md`](docs/LOCAL_TEST_RESEARCH.md). Rather than fight the proxy, lift the deterministic part of the scaffold into a callable function and assert against that directly.

### Added

- **`scripts/scaffold_pipeline.py`** — pure-stdlib helper that copies the bundled payload (`skills/pipeline-init/references/pipeline-payload/`) into a target project root. Public `scaffold(project_root, *, payload_root=None, overwrite=False)` returns a `ScaffoldResult` with the files written. Also callable as CLI: `python scripts/scaffold_pipeline.py <project_root> [--overwrite]`. Honors the same hard rules as SKILL.md (refuses to overwrite existing `.pipelines/` or `scripts/policy/` without `--overwrite`; appends `.agent-runs/` to `.gitignore`).

- **`tests/test_scaffold_pipeline.py`** — 9 deterministic $0 tests covering: payload exists at expected location, fresh-project scaffold produces the same file tree cleanroom-smoke asserts, `.gitignore` updated, existing `.gitignore` entries preserved, idempotent on re-run, refuses overwrite without flag, overwrite flag replaces cleanly, rejects missing project root, rejects missing payload.

- **`docs/LOCAL_TEST_RESEARCH.md`** — captures the local-LLM test-harness investigation. Documents what works (Anthropic→Ollama via `claude-code-router` for simple tool-use), the three walls hit (thinking field, small-model loops, premature stream close), and the conditions under which Tier A would be worth revisiting.

### Changed

- **`--version` strings bumped 1.1.1 → 1.1.2** in `scripts/check_manifest_schema.py` and its bundled payload copy. `test_version_reports_1_1_1` → `test_version_reports_1_1_2`.
- **Manifest versions bumped 1.1.1 → 1.1.2** in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.

### Verified

- pytest full suite: **24 passed in 4.99s** (free tier, up from 15). The cleanroom-smoke test remains opt-in for API-key holders (unchanged behavior).
- New scaffold tests run in 0.51s with $0 spend.
- Coverage gap honestly noted in `tests/test_scaffold_pipeline.py` docstring: the unit test does NOT prove the LLM correctly invokes the skill or that claude CLI's Skill-tool dispatch works. Those remain covered by `test_skill_packaging.py`, `claude plugin list` ✔ enabled, and manual Cowork verification.

## [1.1.1] — 2026-05-12

**Honest fix to the v1.1.0 cleanroom test gap.**

v1.1.0 shipped a `tests/test_cleanroom_install.py` suite that asserted the plugin LOADS in a cleanroom copy. After release, Scott asked the right question:

> "Did the cleanroom REALLY test the pipeline? Wouldn't it make sense to do it with a real repo (or a mocked up real repo) like you did with the handoff skill before?"

He's right. v1.1.0's cleanroom test is a **load test**, not a **use test**. The three tests it added all assert "plugin loads, manifest validates, structure intact" — none of them actually invoke a single skill. If `/agent-pipeline-claude:pipeline-init` were broken — scaffolded to the wrong path, or hung at orientation, or no-op'd — every v1.1.0 "cleanroom" test would still pass. Same regression-class one tier up: v1.0.0/v1.0.1 passed unit tests but didn't load; v1.1.0 cleanroom-load passes but doesn't exercise.

v1.1.1 closes this with a new opt-in test tier.

### Added

- **`tests/test_cleanroom_smoke.py`** — `@pytest.mark.cleanroom_e2e`. Copies the plugin to `tmp_path/plugin/` (no `.git`, no caches), scaffolds a minimum-viable fixture project to `tmp_path/project/` (README.md + CLAUDE.md + HANDOFF.md), then runs `claude --plugin-dir <plugin> -p` from the fixture with a two-turn flow: invoke `agent-pipeline-claude:pipeline-init`, then `APPROVE`. Asserts `.pipelines/` actually scaffolded into the fixture with 9 expected role files + 5 pipeline yamls + 5 policy scripts. **This is the layer that catches "plugin loads but skills no-op" and "skills scaffold to the wrong path"** — failures the load-only tests cannot see.

  Cost: ~$0.05 in Haiku, ~60s wall. Requires `ANTHROPIC_API_KEY` + `claude` on PATH; skips otherwise.

  Run with: `ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_cleanroom_smoke.py -m cleanroom_e2e`

- **`pytest.ini`** — registers the `cleanroom_e2e` marker so pytest doesn't warn.

### Changed

- **Testing taxonomy now six tiers** (was five): split Cleanroom into Cleanroom-load (free, per-commit) and Cleanroom-smoke (opt-in with API key, ~$0.05). [tests/README.md](tests/README.md) rewritten to reflect this honestly, including the gap v1.1.0 had and why it mattered.
- **`--version` strings bumped 1.1.0 → 1.1.1** in `scripts/check_manifest_schema.py`, `scripts/auto_promote.py`, and their bundled copies under `skills/pipeline-init/references/pipeline-payload/scripts/`. Test assertion `test_version_reports_1_1_0` → `test_version_reports_1_1_1` updated to match.
- **Manifest versions bumped 1.1.0 → 1.1.1** in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` (both `metadata.version` and `plugins[0].version`).

### Verified

- pytest full suite: **15 passed in 4.77s** (free tier) + **1 passed in 62.42s** (cleanroom-smoke, opt-in with API key). Total 16 tests.
- Cleanroom-smoke test produces `.pipelines/` with all 9 expected role files, all 5 pipeline yamls, all 3 templates, and `scripts/policy/` with all 5 check scripts. End-to-end against a real `claude -p` session driving a real skill.

### Pipeline behavior unchanged

Role files, manifest schema, scaffold contents, and runtime semantics are all v1.1.0. v1.1.1 is purely a testing-discipline release.

## [1.1.0] — 2026-05-12

**Install/runtime adapter rewrite + bundled-payload merge. Pipeline behavior unchanged.**

This release merges the loader-facing UX fixes from the rewrite/v1.1.0 branch with the bundled-payload structure independently developed in agent-pipeline-codex. The merge addresses both classes of bug found in v1.0.0–v1.0.2: (a) the plugin doesn't load in Cowork, and (b) when it does load, the procedures point at repo-root paths that don't exist in installed caches.

v1.0.0–v1.0.2 had three load-bearing bugs that compounded into "the plugin doesn't work in Cowork":

1. **Plugin skills are namespaced.** Per the [official Claude Code plugin docs](https://code.claude.com/docs/en/plugins), all marketplace plugin skills are invoked as `/<plugin-name>:<skill-name>` — never bare. v1.0 README and USER-MANUAL documented `/run` as the canonical form, which was never reachable. The bare form is reserved for standalone `.claude/commands/` files, not marketplace plugins.
2. **`commands/` and `skills/` were both populated.** v1.0.1 added a `skills/` mirror to make Cowork-loadability "more reliable" without removing `commands/`. The result: every skill registered twice in one plugin, causing Cowork's slash resolver to fail on bare names and the autocomplete to show duplicates.
3. **`marketplace.json` failed validation.** A root `description` field was silently rejected by `claude plugin validate` ("Unrecognized key: description"). Even with the plugin manifest fix from v1.0.2, the marketplace layer above it was still broken.

This release fixes all three structurally and ports the proven packaging pattern from `agent-pipeline-codex`.

### Fixed

- **`.claude-plugin/marketplace.json`** — removed root `description`; moved into `metadata` per the marketplace schema. `claude plugin validate .` now passes.
- **Skills are now namespaced-only.** Documented invocation throughout: `/agent-pipeline-claude:run`, `/agent-pipeline-claude:pipeline-init`, `/agent-pipeline-claude:audit-init`. README, USER-MANUAL, and ARCHITECTURE rewritten.
- **`/agent-pipeline-claude:run` actually loads in Cowork.** Verified via `claude plugin list` showing `Status: ✔ loaded` at v1.1.0 against an isolated `--plugin-dir` test copy.

### Removed

- **`commands/` directory entirely.** v1.1 ships only `skills/`. Anthropic's [plugin docs](https://code.claude.com/docs/en/plugins) explicitly recommend `skills/` for new plugins ("`commands/`: Skills as flat Markdown files. Use skills/ for new plugins").
- **`commands/new-run.md` + `commands/run-pipeline.md` shims** — deprecation period over. They were marked for v1.1 removal in v1.0; never functional in Cowork because v1.0.0–v1.0.2 never loaded.
- **`skills/new-run/` + `skills/run-pipeline/`** — same.

### Changed (structural)

- **Skills are self-contained per Codex's pattern.** Each `skills/<name>/SKILL.md` is a thin shim (~25 lines) with frontmatter + tool-mapping notes; the canonical procedure lives in `skills/<name>/references/<name>.md`. Enables progressive disclosure (Anthropic's recommended pattern) and prevents `../../commands/...` traversal that breaks once skills are copied to install locations.
- **Bundled audit templates inside `skills/audit-init/references/`.** The three templates (`audit-gate-template.md`, `audit-protocol-template.md`, `5-lens-self-audit-template.md`) ship inside the skill folder. The procedure file points at the bundled copies, not at the repo-root `pipelines/templates/` path that won't resolve from installed caches.
- **Bundled the entire pipeline payload at `skills/pipeline-init/references/pipeline-payload/`.** All 32 files (pipeline definitions, role files, policy scripts) the `/pipeline-init` skill scaffolds into a consumer's project now live inside the skill folder. The procedure copies from the bundled payload, not from `pipelines/` or `scripts/` at repo-root.
- **`scripts/check_skill_packaging.py`** — validator originally ported from `agent-pipeline-codex/scripts/check_skill_packaging.py`. **Deepened in v1.1.0** to scan every `*.md` file under each skill recursively (not just `SKILL.md`), with a discriminating regex that flags plugin-owned paths (`pipelines/`, `scripts/`) while skipping consumer-project paths (`docs/`, `tests/`, `CLAUDE.md`) and bundled-payload templates (paths inside `references/pipeline-payload/`). Catches the procedure-level wiring bugs that the original shallow scan missed.
- **`tests/test_skill_packaging.py`** — pytest wrapper around the packaging check. Part of the standard test gate.
- **Policy script `--version` strings bumped 1.0.0 → 1.1.0** in `auto_promote.py` and `check_manifest_schema.py` (both top-level and bundled copies under the pipeline payload). Confirms which release a `/pipeline-init`-scaffolded project came from.

### Added (v1.1.0 polish)

These commits land on top of the v1.1.0 install/runtime adapter rewrite to close two trust gaps Scott called out after the initial merge: no automated cleanroom test, no comprehensive testing taxonomy.

- **`tests/test_cleanroom_install.py`** (NEW, 3 tests). Closes the "no automated cleanroom test" gap. Copies the plugin to a `tmp_path/agent-pipeline-claude/` (no `.git`, no `__pycache__`, no `.agent-runs/`, no `installed_plugins.json` entries) and exercises three load-bearing paths against it:
  1. `claude --plugin-dir <copy> plugin list` → asserts `Status: ✔ loaded` + manifest version match.
  2. `claude plugin validate` against both manifests in the cleanroom copy.
  3. `check_plugin_structure.py` run from inside the cleanroom copy.

  All three skip gracefully if `claude` CLI is not on PATH. **This is the layer that catches the v1.0.0/v1.0.1 regression class** — manifest valid in isolation but the install layout silently rejected by the loader — that unit + smoke tests would miss. Test suite is now **15 passed** (was 12).

- **`tests/README.md` rewritten with five-tier testing taxonomy**:
  - **Static**: manifest validation + structure check + skill-packaging scan (ms, $0/commit).
  - **Unit**: pytest on policy scripts (seconds, $0/commit).
  - **Smoke**: skill self-containment in installed-cache shape (seconds, $0/commit).
  - **Cleanroom**: `--plugin-dir <copy>` load + validate from a fresh copy (seconds, $0/commit).
  - **End-to-end**: full `/run` orchestration against a fixture, real model spend (minutes, ~$2 Haiku / ~$15 Sonnet, tags + nightly).

  The doc spells out which tier catches which failure class, includes the E2E procedure with the actual `claude -p --session-id` multi-turn flow, and documents the fixture-pollution caution (`/run`'s executor materializes files matching `manifest.allowed_paths` into the fixture and pytest will fail on those generated test files unless the fixture is cleaned afterwards via `git clean -fdx tests/fixtures/<name>/`).

- **`docs/index.html` rewritten for v1.1.0**. The v0.5 "museum-quality" landing page (706 lines, custom typography) was stuck at v1.0.0. Replaced with a restrained, honest v1.1.0 page (280 lines, system fonts, zero external deps, dark-mode aware) focused on what the plugin actually does, the five-tier testing tiers as trust signal, and the honest caveats (Haiku-vs-Sonnet format drift, no CI E2E, `run.log` gap). The old landing page is preserved in git history at commit `1cffff4` (`design(landing): museum-quality rebuild — Schematic Restraint`) — pull it back via `git checkout 1cffff4 -- docs/index.html` if preferred.

- **`.gitignore`** adds `.claude/` (some Claude Code surfaces write a `.claude/` dir alongside the repo; never commit it).

### Verification evidence

```
$ claude plugin validate ~/.claude/plugins/marketplaces/agent-pipeline-claude
Validating marketplace manifest: ...\.claude-plugin\marketplace.json
✔ Validation passed

$ python scripts/check_skill_packaging.py
SKILL-PACKAGING: PASSED (3 skills validated)

$ python tests/check_plugin_structure.py
Plugin structure check
  skills:    3
  commands:  0
  roles:     14
  pipelines: 5
ALL CHECKS PASSED

$ claude --plugin-dir /tmp/agent-pipeline-claude-test plugin list
Session-only plugins (--plugin-dir):
  ❯ agent-pipeline-claude@inline
    Version: 1.1.0
    Status: ✔ loaded

$ python -m pytest tests/ -v
tests/test_check_manifest_schema.py ........... [11 PASSED]
tests/test_cleanroom_install.py ...            [3 PASSED]
tests/test_skill_packaging.py .                [1 PASSED]
======================== 15 passed ========================
```

### Action required

After upgrading the plugin install to v1.1.0:

1. **Fully quit and restart Cowork** (Quit/Exit, not just close the conversation window).
2. Use the namespaced form: `/agent-pipeline-claude:run "..."`. The bare `/run` form documented in v1.0 was never reachable.
3. If you scripted against `/new-run` or `/run-pipeline`, replace with `/agent-pipeline-claude:run`.

Pipeline behavior, manifest schema, role files, and policy scripts are unchanged from v1.0.

## [1.0.2] — 2026-05-11

**Critical manifest fix — v1.0.0 and v1.0.1 never actually loaded.**

`claude plugin list` reveals the real story: in v1.0.0 and v1.0.1, the plugin manifest at `.claude-plugin/plugin.json` had `repository` as an npm-style object (`{"type": "git", "url": "..."}`), but Claude Code's plugin schema requires a plain string. The loader **rejected the entire manifest**:

```
Status: ✘ failed to load
Validation errors: repository: Invalid input: expected string, received object
```

A failed manifest means the plugin's commands AND skills never register. That's why `/run` did nothing in Cowork for two releases — not because of `commands/` vs `skills/` layout, not because of YAML parse errors (those existed too, but were secondary). The primary bug was that the entire plugin failed to load at the manifest stage.

Also surfaced: latent YAML parse error in `commands/run.md` and `skills/run/SKILL.md` `argument-hint` line (closing quote followed by unquoted text — invalid YAML). Inherited from v0.5.2 in commands/, copied forward into skills/ during v1.0.1. Would have broken /run specifically even after the manifest loaded.

### Fixed

- **`.claude-plugin/plugin.json` `repository` field** — changed from npm-style `{type, url}` object to plain string per Claude Code plugin schema. This single change unblocks the entire plugin.
- **`commands/run.md` frontmatter** — `argument-hint` rewritten as single-quoted YAML scalar so it parses cleanly.
- **`skills/run/SKILL.md` frontmatter** — same fix.

### Added

- **`tests/check_plugin_structure.py`** — comprehensive validator that runs every commit-worthy plugin artifact through real YAML/JSON parsers and checks required fields. Catches both bug classes from v1.0.0–v1.0.1 in one pass. Use before any push that touches manifests, commands, skills, or roles.

### Verification

`claude plugin list` after the v1.0.2 patch shows `Status: ✔ enabled`. (Same load succeeds in Cowork — Cowork uses the same plugin loader.)

### Action required for v1.0.0 / v1.0.1 users

After upgrading the install to v1.0.2, **fully quit Cowork and restart** (not just open a new conversation — the plugin metadata is read at app startup). Then `/run status` should be recognized.

## [1.0.1] — 2026-05-11

Critical Cowork compatibility fix. v1.0.0 shipped the primary `/run` entry point as a plugin command under `commands/run.md`, but Cowork only loads plugin-provided user commands from the `skills/<name>/SKILL.md` layout. Net result on v1.0.0: every Cowork user typing `/run` saw *"/run isn't a recognized command here. Some commands only work in the Claude Code terminal."* — making the plugin's primary UX unreachable for the audience it was specifically rewritten for.

Surfaced the first time `/run` was exercised in a live Cowork session (CivicCast Slice 1 Commit 8 test-drive, 2026-05-11). Fix is purely additive — same content, correct loader-visible layout.

### Fixed

- **`/run`, `/pipeline-init`, `/new-run`, `/run-pipeline`, `/audit-init` now load in Cowork.** Each command is mirrored as `skills/<name>/SKILL.md` (identical body content, frontmatter gains a `name:` field). The legacy `commands/*.md` files are kept for any CLI consumer that still scans them, but the `skills/` layout is the authoritative path going forward — it's the layout the Anthropic claude-plugins-official examples use and the one Cowork's plugin loader discovers.
- **Documented Cowork command-loading reality.** The v1.0.0 README and `pipeline-init.md` Step 4 claimed slash commands "register at session start" without distinguishing the `commands/` vs `skills/` layouts. This patch supersedes that claim mechanically by shipping the correct layout; a documentation pass to update the prose is queued for v1.0.2.

### Preserved unchanged

- All v1.0.0 content, role files, policy scripts, schema validator, drafter logic, orchestration rules, gate semantics, and pipeline definitions. The patch only changes file layout, not behavior.

### Action required for v1.0.0 users

After upgrading the plugin install to v1.0.1, restart your Cowork session so the newly-discoverable `skills/` directory is picked up. Then type `/run status` to confirm registration.

## [1.0.0] — 2026-05-11

UX rewrite. The v0.5 hardening mechanism (critic + drift-detector + judge layer + auto-promote + strict schema) is preserved unchanged; everything around it that touches a user is rebuilt around four load-bearing decisions: Cowork-first, spec-aware drafting, one command, chat-native gates.

Built from a real audit run during this very session — a CivicCast operator hit "/plugin isn't available" at the install step, then "really really bad UX" at the manifest fill-in step, then asked to rewrite the plugin. v1.0 is that rewrite.

### Added

- **`/run` slash command** (`commands/run.md`). Single entry point replacing v0.5.2's two-step `/new-run` + `/run-pipeline`. Argument shapes: `/run "<description>"` to start, `/run resume <run-id>` to pick up, `/run status` to list. Drafts a manifest from your project's spec, presents it in chat, loops on revision, then orchestrates the pipeline end-to-end.
- **Manifest drafter role** (`pipelines/roles/manifest-drafter.md`). A new fresh-context subagent the `/run` orchestrator invokes before any stage. The drafter walks the project root for spec / release-plan / scope-lock / design-note / ADR / ledger artifacts, then auto-derives 9 of 11 manifest fields from those sources. Writes `manifest.yaml` + `draft-provenance.md` (per-field source attribution). Returns a one-line summary string for the orchestrator's chat prompt.
- **Spec source-walking protocol** documented in `manifest-drafter.md`. Recognizes `*UnifiedSpec*.md`, `SPEC.md`, `PRD.md`, `*ReleasePlan*.md`, `ROADMAP.md`, `docs/releases/v*-scope-lock.md`, `docs/research/<version>-design.md`, `docs/adr/*.md`, `CLAUDE.md`, `audit-*/`. Operators can also override discovery via a `## Pipeline drafter notes` section in `CLAUDE.md`.
- **Cowork-first install instructions** in `README.md` and `USER-MANUAL.md`. A paste-able bootstrap prompt the user drops into any Claude session — Cowork or CLI — that does the file-level install (clone + JSON patches). The CLI-only `/plugin install scottconverse/agent-pipeline-claude` becomes a convenience note for users whose build has the command available.
- **Explicit restart-required signal post-install.** Every install path ends with the user being told to restart their session before the slash commands register. v0.5.2 didn't surface this; v1.0 makes it a first-class step.
- **Chat-native gate prompts** at the three human gates (manifest / plan / manager). Three universal verbs: `APPROVE`, `REPLAN <description>` (or freeform changes), `BLOCK` (manager gate only). Replaces v0.5.2's modal `AskUserQuestion` popups for the three gates. `AskUserQuestion` stays available for genuinely-disambiguating mid-stage questions.
- **Failure-message shape with remediation pointers.** Every error surface — `check_manifest_schema.py` failures, missing manifests, schema-rejected drafts, stage failures — follows a standard shape: *what failed / where / current value / suggestion / full-context pointer*. No raw Python tracebacks reach the user.
- **`commands/run.md` Step 6 five-state walkthrough.** The manifest-review screen has five explicit states (populated draft, greenfield, partial, schema-error, loading) so the orchestrator never surprises the user with an unfamiliar shape.

### Changed

- **`commands/pipeline-init.md` rewritten.** Now produces a one-message orientation summary first, asks for APPROVE, then scaffolds. The `CLAUDE.md` starter is shorter and explicitly includes a `## Pipeline drafter notes` section telling the drafter where this project keeps its spec / release plan / design notes / ledgers. The legacy "11-section template CLAUDE.md scaffold" is gone — projects fill in what they need.
- **`scripts/check_manifest_schema.py` error-message rewrite.** Each violation now surfaces with field name + problem + current value + concrete suggestion, plus a footer telling the operator which file to edit and which `/run resume` command to re-trigger validation. The schema *rules* are unchanged from v0.5.2; only the human-facing output shape changed.
- **Plugin manifests (`plugin.json` + `marketplace.json`) bumped to v1.0.0** with new short descriptions matching the redesign.
- **README.md fully rewritten.** Journey-first ("here's what a `/run` looks like") instead of stage-list-first. Cowork-first install. Concrete examples of a drafted manifest inline. Migration story for v0.5.x users.
- **USER-MANUAL.md updated** to match the new surface. Restart-required surfaced. Greenfield fallback path documented. Two universal verbs (`APPROVE` / `REPLAN`) introduced before any walkthrough.

### Deprecated

- **`/new-run` and `/run-pipeline`** survive as deprecated shims at v1.0.0. Each prints a one-paragraph deprecation notice on invocation, offers to delegate to `/run`, and only falls through to legacy v0.5.2 behavior if the user replies `LEGACY`. **These shims will be removed at v1.1.** v0.5.2 muscle-memory has one minor release of soft cutover.

### Preserved unchanged

- The manifest schema (`pipelines/manifest-template.yaml` + `check_manifest_schema.py` rule set).
- All 13 v0.5.2 role files (`researcher`, `planner`, `test-writer`, `executor`, `verifier`, `drift-detector`, `critic`, `manager`, `judge`, `preflight-auditor`, `local-rehearsal`, `cross-agent-auditor`, `implementer-pre-push`).
- The six policy scripts (`check_manifest_schema`, `check_allowed_paths`, `check_no_todos`, `check_adr_gate`, `auto_promote`, `run_all`).
- The three pipeline definitions (`feature.yaml`, `bugfix.yaml`, `module-release.yaml`).
- The `.agent-runs/<run-id>/` artifact stack (research.md, plan.md, verifier-report.md, etc.).
- The opt-in judge layer (file-presence activation via `.pipelines/action-classification.yaml`).
- The six-condition auto-promote check.
- The v0.3 audit-handoff scaffold (`/audit-init` + the three out-of-repo + in-repo audit artifacts).

### Migration from v0.5.x

1. `cd ~/.claude/plugins/marketplaces/agent-pipeline-claude && git pull && git checkout v1.0.0`.
2. Restart your Cowork or CLI session.
3. The `installed_plugins.json` entry should auto-update on next plugin scan. If it doesn't, manually update `gitCommitSha` to the v1.0.0 tag SHA.
4. Your existing `.pipelines/`, `scripts/policy/`, `CLAUDE.md`, and `.agent-runs/` are unchanged. v1.0 reads them as-is.
5. Your old `/new-run` + `/run-pipeline` muscle memory still works (with a deprecation notice) through v1.0. New runs should use `/run`.

### Compatibility caveats

- The shape of `manifest.yaml` is unchanged. Old manifests at `.agent-runs/<run-id>/manifest.yaml` from v0.5.x still validate against v1.0.
- The `.agent-runs/<run-id>/` artifact filenames are unchanged. Old runs can be resumed via `/run resume <run-id>`.
- The new manifest-drafter writes one additional artifact (`draft-provenance.md`) alongside the manifest. This is informational; no other stage reads it.

## [0.5.2] — 2026-05-11

Rename + scope-narrowing release. The plugin is now `agent-pipeline-claude` (was `agentic-pipeline`) and is published as a Claude Code plugin only. Codex Desktop App support has been removed from the upstream repo and will live in a separate downstream repo.

### Changed

- **Plugin rename.** `agentic-pipeline` → `agent-pipeline-claude` across all live files: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, the four policy scripts' `--version` strings, README / USER-MANUAL / ARCHITECTURE / CHANGELOG bodies, `docs/index.html` (landing page), discussion seed posts, command and role-file references. Historical CHANGELOG entries (v0.1-beta through v0.5.1) describe the same plugin under its previous name for continuity.
- **Repo rename.** GitHub repo renamed `scottconverse/agentic-pipeline` → `scottconverse/agent-pipeline-claude`. GitHub redirects clone URLs and HTTP routes for the old name automatically; existing `/plugin install scottconverse/agentic-pipeline` references break only on plugin-marketplace tooling that doesn't follow HTTP redirects.
- **Codex Desktop App support removed.** The Codex parallel implementation (skill files, `.codex-plugin/plugin.json`, `pipelines/templates/AGENTS.md`, `docs/codex-desktop-adaptation.md`) is not part of this release. Will be re-published as a separate downstream repo. References to Codex in audit-handoff documentation have been genericized — the dual-AI audit pattern (v0.3) still works with any second AI, but the plugin no longer ships Codex-specific scaffolding.
- **`pipelines/roles/drift-detector.md` invariant 8a simplified.** The version-string-consistency invariant now describes a single plugin manifest (no Codex-side parallel). Clearer rules, no adapter-suffix edge cases.

### Why this release exists

The Codex Desktop App side and the Claude Code side had been entangled in one repo, with parallel plugin manifests, parallel orchestration logic, and a version-string invariant that had to handle both. The two sides have different runtime models, different distribution mechanisms, and different release cadences. Separating them removes the entanglement and lets each side ship on its own schedule.

`0.5.2` rather than `0.6.0` because the surface area of the plugin (commands, roles, policy scripts, pipeline definitions, run state) is unchanged. The change is purely identity (name) and scope (single-runtime).

### Migration

Existing installs:

```bash
# Update the plugin reference
/plugin uninstall agentic-pipeline
/plugin install scottconverse/agent-pipeline-claude
```

Projects already initialized with `/pipeline-init` continue to work — the scaffolded `.pipelines/`, `scripts/policy/`, and `.agent-runs/<run-id>/` files are unchanged. Re-running `/pipeline-init` to refresh the scaffolded scripts is recommended but not required.

The GitHub repo URL change propagates automatically via GitHub's redirect for git clone, but bookmarks to `https://github.com/scottconverse/agentic-pipeline` and `https://scottconverse.github.io/agentic-pipeline/` should be updated to use the new name.

---

## [0.5.1] — 2026-05-11

Patch release. Adds standing doc-currency invariants to the drift-detector role so cumulative drift cannot ship under a feature-scoped manifest.

### Why this release exists

v0.5's drift-detector was contracted against the manifest's `expected_outputs` only. That contract is sound for the run's own scope but lets cumulative drift accumulate across releases: a feature-scoped manifest legitimately ships its feature, the verifier passes, but the project's top-of-file content (counts, tables, diagrams, version strings, section orderings) goes stale because nothing in the manifest names the back-audit.

The v0.5 dogfood run (`.agent-runs/2026-05-11-version-flag/`) had this exact gap — the drift-detector caught the in-scope `--version` drift but did not flag months-stale top-of-file content in README, USER-MANUAL, and `docs/index.html`. The fix is structural: invariants every release silently makes get their own enforcement, independent of any manifest.

### Changed

- `pipelines/roles/drift-detector.md` — added §8 **Standing doc-currency invariants**. Five invariants checked on EVERY run regardless of manifest scope:
  - **8a Version-string consistency** (`blocker` on mismatch): every authoritative version string in the repo agrees — `plugin.json`, `marketplace.json` (top-level metadata AND each plugin entry), `pyproject.toml` if present, every `argparse action="version"` string under `scripts/`, the top `## [X.Y.Z]` in `CHANGELOG.md`, `<div class="badge">vX.Y.Z` in `docs/index.html`, `**Version:** X.Y.Z` in `USER-MANUAL.md`.
  - **8b File-inventory tables** (`non-blocker` on small drift, `blocker` on whole-release-missing): USER-MANUAL "What you get" counts match `ls` reality; README scaffold block lists every file actually in `pipelines/roles/` and `scripts/`.
  - **8c Pipeline-diagram parity** (`blocker` on docs releases): `docs/index.html` `.pipeline-diagram` stages match `pipelines/feature.yaml` stage list and order.
  - **8d Section-ordering sanity** (`non-blocker`): per-version sections in README and USER-MANUAL appear in monotonic order; a `## v0.5:` followed by a `## v0.4:` is a reliable back-audit signal.
  - **8e Stability-posture currency** (`non-blocker`): any explicit current-release version reference in `docs/index.html` matches the current release.
- Output checklist updated to require explicit PASS/FAIL on every standing invariant.
- Drift item numbering shifted: §8 was Drift items; now §9. The count line in §2 was already abstracted across all numbered drift sections, so this is a forward-compatible rename.
- `--version` flag bumped to `agent-pipeline-claude 0.5.1` on `scripts/auto_promote.py` and `scripts/check_manifest_schema.py`.

### Stacking with v0.2, v0.3, v0.4, v0.5

No new stages, no new role files, no new policy scripts. v0.5.1 extends the contract of the existing drift-detector role file. All v0.5 behavior is preserved; the only behavioral change is that more drift items will be flagged on future runs.

### Honest limit

The standing invariants are enforced by a role file the drift-detector subagent reads. A subagent could in principle disregard a hard rule. The structural backstop is `auto_promote.py`'s read of the drift count line — if the drift-detector reports blocker drift, auto-promote refuses to fire, and the manager human-approval gate runs. The invariants harden the role contract but do not replace the auto-promote gate.

---

## [0.5.0] — 2026-05-11

The single-AI hardened release. Six structural changes that compensate for dropping dual-AI cross-family verification: two new agent roles (critic, drift-detector), pre-edit fact-forcing in the executor, expanded judge classification, machine-checkable auto-promote, and strict manifest schema validation. Built from the design question "can the pipeline do both action-level judge AND post-hoc audit with one AI?" Answer: yes, with the structural defense in this release.

The release is a structural substitute for the dual-AI audit-handoff discipline (v0.3) when running with a single AI. Existing dual-AI projects keep working; nothing in this release removes capability. The shipped honest limit: same-model-family verification cannot fully replace cross-family verification. The CHANGELOG entry below names the residual risk and the recommended mitigation.

### Added

- `pipelines/roles/critic.md` — adversarial critic role file. Fires after the verifier in a fresh context. Reads every artifact cold and produces a structured findings report with a parseable §2 count line (`**Findings: T total, B blocker, C critical, M major, N minor**`). Walks six adversarial lenses: engineering, UX, tests, docs, QA, scope. Hard rules forbid encouragement, severity softening, "no findings" without per-lens evidence, and trusting the verifier or executor at face value. Structural substitute for cross-family verification in single-AI runs.
- `pipelines/roles/drift-detector.md` — drift-detector role file. Fires after the verifier (before the critic). Compares manifest fields against the final assembled state. Catches the gap class neither the judge (per-action) nor the verifier (per-criterion) can see — durable doc drift, cross-file consistency, status-word abuse, ledger top-totals vs row counts, "Closed" without evidence. Emits parseable §2 count line (`**Drift: T total, B blocker**`).
- `scripts/check_manifest_schema.py` — manifest schema validator. Wired into both run-pipeline.md Phase A2 (run-start, before any stage fires) and `scripts/run_all.py` CHECKS (policy stage, defense in depth). Rules: `goal` >= 30 chars, `definition_of_done` >= 80 chars, `expected_outputs` non-empty, `non_goals` non-empty, `rollback_plan` non-empty, broad `allowed_paths` requires non-empty `forbidden_paths`, forbidden status words (`done`, `complete`, `ready`, `shippable`, `taggable`) banned from goal/dod. The fuzzy-manifest class of failure now blocks at the gate before it cascades into downstream work.
- `scripts/auto_promote.py` — machine-checkable promote decision. Reads verifier-report.md, critic-report.md, drift-report.md, policy-report.md, judge-metrics.yaml (when present), and implementation-report.md. Evaluates six conditions: verifier-clean (zero NOT MET, zero PARTIAL), critic-clean (zero blocker, zero critical), drift-clean (zero blocker), policy-passed, judge-clean (zero judged_block, zero human_blocked, vacuous when judge inactive), tests-passed. When all six pass, writes a preset `manager-decision.md` with `**Decision: PROMOTE**` and a citation block; otherwise writes `auto-promote-report.md` naming the failing conditions and exits 1.
- `--version` flag on `scripts/auto_promote.py` and `scripts/check_manifest_schema.py`. Operators run either script with `--version` to print `agent-pipeline-claude 0.5.0` and confirm which release is installed. The flag uses argparse's built-in `action="version"`, so it fires before required-arg validation — `auto_promote.py --version` works without supplying `--run`. Output is `agent-pipeline-claude 0.5.0` on stdout, exit code 0. Added as the deliverable of the v0.5 self-dogfood pipeline run (`.agent-runs/2026-05-11-version-flag/`, gitignored), which exercised every new v0.5 stage end-to-end and validated the auto-promote short-circuit.

### Changed

- `pipelines/roles/executor.md` — added a "Pre-edit fact-forcing gate" section. Before the first edit/write to any file in the run, the executor must produce a fact block (importers/callers, public API affected, data schema touched, manifest goal quoted verbatim) either inline in `implementation-report.md` or in `.agent-runs/<run-id>/notes/pre-edit-<filename>.md`. The drift-detector and critic stages check for the block and treat its absence as a finding on any touched file.
- `pipelines/roles/verifier.md` — added §0 "Criteria count line" requirement. The verifier must emit `**Criteria: T total, M MET, P PARTIAL, N NOT MET, A NOT APPLICABLE**` as a parseable line so `auto_promote.py` can read the verdict count without scanning the full report.
- `pipelines/roles/manager.md` — added "Auto-promote awareness (v0.5)" section. When `manager-decision.md` already exists with `**Decision: PROMOTE**` as the first line (auto-promote preset), the manager runs in validate-and-append mode instead of re-deciding. Inputs list extended with drift-report.md, critic-report.md, auto-promote-report.md, and judge-log.yaml/judge-metrics.yaml when present. PROMOTE criteria extended: critic blocker/critical = 0, drift blocker = 0, judge judged_block + human_blocked = 0.
- `pipelines/action-classification.yaml` — five new patterns under `high_risk`: `npm install --global` and `npm install -g`, `sudo`, `pip install` (non-editable, non-user), `git commit` with BREAKING in the message. Each tightens action-time defense against the failure modes operators most commonly cite.
- `pipelines/feature.yaml` — three new stages between `verify` and `manager`: `drift-detect`, `critique`, `auto-promote`. Manager stage gets `auto_promote_aware: true` flag.
- `pipelines/bugfix.yaml` — same three new stages and the manager flag.
- `pipelines/module-release.yaml` — three new phases between `phase4-verify` and `phase5-manager`: `phase4b-drift-detect`, `phase4c-critique`, `phase4d-auto-promote`. `phase5-manager` gets `auto_promote_aware: true`.
- `commands/run-pipeline.md` — Phase A2 now invokes `check_manifest_schema.py` before any stage runs. Handler 2 (`role: pipeline`) handles `optional_artifact: true` for the auto-promote stage. New **Handler 4** for `role: manager` with `auto_promote_aware: true`: checks for the preset, short-circuits the human gate when present, falls through to standard Handler 3 + Handler 1 when absent.
- `scripts/run_all.py` — `check_manifest_schema` added to `CHECKS` list. Runs first so a fuzzy manifest fails the policy stage even if it slipped past Phase A2.

### Why each piece exists

- **Critic stage.** v0.4's judge catches per-action scope violations in real time, but doesn't read the assembled output. The verifier reads the assembled output, but in the same model family as the executor — correlated blind spots are exactly the class of failure neither catches. The critic runs in a fresh context with a deliberately adversarial role contract.
- **Drift-detector stage.** Drift between manifest contract and durable artifacts is invisible to per-action and per-criterion verification. It only surfaces when you compare the manifest's promises to the assembled final state.
- **Pre-edit fact-forcing in executor.** Asking an LLM "are you sure?" is useless. Demanding concrete artifacts (importer list, schema, instruction quote) forces investigation that catches blast-radius surprises before they hit the verifier.
- **Expanded judge classification.** Global npm installs leak project-level promises into system-level state; sudo escalations sidestep manifest scope; non-editable pip installs in shared environments produce non-reversible side effects; BREAKING-marked commits are semver-major signals deserving explicit confirmation.
- **Machine-checkable auto-promote.** The manager gate becomes auto-firing when all six structural conditions hold. Humans get the time back without losing the gate — when any condition fails, the human gate is still there.
- **Strict manifest schema validation.** Every drift cascade investigated in prior projects traced back to a fuzzy manifest. The schema check makes the fuzzy state fail-fast.

### Stacking with v0.2, v0.3, v0.4

- v0.2 module-release pipeline: catches execution-cascade failures (pre-existing CI bugs, tag-move dances, halt-and-ask loops). Pre-executor.
- v0.3 dual-AI audit-handoff: catches drift failures via cross-family separation of duties. Post-executor, separate session.
- v0.4 judge layer: catches unauthorized actions in real time. During executor.
- **v0.5 hardened single-AI**: catches the drift class without needing a second AI, at the cost of accepting some correlated blind spots. During verify -> drift-detect -> critique -> auto-promote.

Use v0.3 when you have two model families available. Use v0.5 when you want single-AI operation. The two stack — projects can run both, with v0.3's cross-family audit firing on a sample of v0.5 runs.

### Known limitations

- **Correlated single-model-family blind spots.** Critic and verifier are both same-model-family. If both agents share a wrong assumption that fits the manifest, both sign off and the auto-promote fires green. Dual-AI is the only structural defense against this. Mitigation: periodic sample audit by a different model family on a weekly cadence or after every Nth run. The v0.3 `/audit-init` discipline still applies.
- **Auto-promote depends on parseable count lines.** The verifier, critic, and drift-detector role files explicitly require the count-line format. If a role file is customized in a way that drops or malforms the line, auto_promote.py treats the run as NOT_ELIGIBLE and falls back to the human gate.
- **The judge layer still fires only on the executor stage.** Even with v0.5 active, the judge does not intercept critic, drift-detector, or verifier actions. Those roles are read-only by contract.
- **Schema validation cannot verify manifest correctness, only structure.** A confident-wrong manifest that satisfies every schema rule still produces wrong work. The manifest gate (human) remains the only place the manifest's content is reviewed.

## [0.4.0] — 2026-05-11

The judge layer. Real-time action-level supervision inside the executor stage. Built from Nate Jones, "LLM-as-Judge" (May 2026). The Lindy case study — an agent that sent 14 unauthorized emails because operator-trained-reflex APPROVE clicking defeated manual confirmation — showed that prompts don't hold across long context, and per-action confirmation alone breeds the cookie-banner effect. The architectural fix is a second agent (the judge) whose sole loyalty is the manifest, evaluated in context isolation from the executor's reasoning chain.

### Added

- `pipelines/roles/judge.md` — role file for the judge subagent. Returns exactly one of four verdicts: `allow`, `block`, `revise`, `escalate`. Output is a single YAML block, no prose. Hard rules forbid helping the executor, negotiating, inferring authorization, summarizing, deferring to executor confidence, approving by precedent, or modifying anything outside the verdict file. Inputs are deliberately scoped: manifest, matched action policy, prior judge decisions for the run, and the structured action proposal — but **not** the executor's reasoning chain. Context isolation is the mechanism.
- `pipelines/action-classification.yaml` — opt-in classification rules. Four risk classes (`read_only`, `reversible_write`, `external_facing`, `high_risk`) with first-match-wins evaluation top-to-bottom within each class. Class priority `high_risk` → `external_facing` → `reversible_write` → `read_only`. Default class for unmatched actions: `reversible_write`. Ships with the common dangerous and external-facing patterns: `rm -rf`, `git push --force`, `git push main`, `DROP TABLE`, `npm publish`, `gh pr create`, `curl -X POST`, `docker push`, `kubectl apply`, credential-touching `export *KEY=`, etc.
- `commands/run-pipeline.md` — **Handler 3a** for the executor stage when `.pipelines/action-classification.yaml` exists. Wraps the executor in a classify → judge → execute inner loop. Routes by class: `read_only` and `reversible_write` execute immediately + log; `external_facing` requires judge ALLOW; `high_risk` requires judge ALLOW plus human confirm. Verdict routing: `allow` executes, `block` halts the pipeline, `revise` returns a concrete revision instruction (max 3 cycles per action_id; auto-escalate after), `escalate` pauses for a specific human question. Handler 3 (the v0.3 executor handler) is preserved unchanged and is selected when `action-classification.yaml` is absent — the layer is opt-in by file presence.
- `judge-log.yaml` artifact — chronological per-action record written to the run directory when the judge layer is active. Captures tool, arguments, class, disposition (`auto_allow` / `judged_allow` / `judged_revise` / `judged_block` / `judged_escalate` / `human_confirmed` / `human_blocked`), judge verdict, judge reason, revision instruction, and timestamp.
- `judge-metrics.yaml` artifact — aggregate counts plus `escalation_rate`, `judge_invocations`, `revision_cycles`. Written alongside `judge-log.yaml` at executor-stage end. The escalation rate is the operator's tuning signal — see USER-MANUAL.md §"The judge layer (v0.4)".
- `judge-decisions/<action_id>.yaml` directory — per-action verdict files written by the judge subagent. Read by the next judge invocation as `prior_judge_decisions` so re-proposals of blocked actions are detected without depending on conversation memory.
- ARCHITECTURE.md §7 — full design rationale, the classify → judge → execute inner-loop diagram, the context-isolation diagram, the relationship-to-other-gates table, the four-verdicts spec, and the opt-in-by-file-presence model.
- README.md §"v0.4: Judge layer" — quickstart overview with the four classes, the four verdicts, and the artifacts produced. One-line summary: "Pipeline (v0.2) catches execution-cascade failures. Audit-handoff (v0.3) catches drift failures. Judge layer (v0.4) catches unauthorized actions in real time."
- USER-MANUAL.md §"The judge layer (v0.4)" — operator-facing guidance. Enabling and disabling, customizing rules per project, reading `judge-log.yaml` and `judge-metrics.yaml`, escalation-rate tuning (too-low vs too-high vs healthy range 0.02–0.10), adding project-specific high-risk patterns, and what to do when the judge ESCALATEs and you aren't sure (don't reflex-APPROVE — fix the manifest ambiguity).

### Why each new piece exists

- **Context isolation prevents single-model-family correlated judgment.** The judge cannot be persuaded by the same chain of inferences that produced the over-broad action because it never sees them. The manifest and the action proposal are the inputs; the executor's reasoning chain is deliberately withheld.
- **Four verdicts, not two, prevent BLOCK fatigue.** ALLOW / BLOCK alone forces the judge into binary calls on actions where a small revision (push to feature branch, not main) would make the action correct. `revise` and `escalate` give the judge ways to be useful without halting the pipeline on every borderline call.
- **High_risk + human confirm even after judge ALLOW is belt-and-suspenders.** A judge ALLOW on `rm -rf` or `git push --force` is still a moment a human should see. The judge's ALLOW raises confidence that the action is authorized; the human confirm is the final check that no inferred-authorization slipped through.
- **Opt-in by file presence keeps the layer composable.** Projects that don't need real-time action supervision (small repos, no external services, mature contributor base) can run without the file and get v0.3 behavior. Projects that need it add one YAML file. There is no separate `/enable-judge` command and no manifest field to remember.
- **Metrics in `judge-metrics.yaml`, not just logs, give the operator a tuning loop.** Without an aggregate, operators can't tell whether they're at 0% escalation (rules too loose) or 30% (cookie-banner fatigue forming). The `escalation_rate` is computed every run; reviewing it after the first 5–10 runs typically converges the rules to a healthy range.

### Stacking with v0.2 and v0.3

- Pipeline (v0.2) catches execution-cascade failures: pre-existing CI bugs, tag-move dances, halt-and-ask loops. Pre-executor.
- Audit-handoff (v0.3) catches drift failures: wrong endpoint, stale CHANGELOG, status-word abuse. Post-executor.
- **Judge layer (v0.4) catches unauthorized actions in real time.** **During** the executor stage, at the action boundary, before the action lands. The other two run before and after the executor; the judge runs **during**.

The three layers address three different failure classes and can be enabled independently. Many projects will run all three.

### Known limitations

- **Single-model-family blind spots may still correlate.** The judge is a subagent of the same model family as the executor. Some classes of failure (e.g., a particular phrasing that biases both agents identically) can persist. The architectural defense (context isolation) reduces this risk; it does not eliminate it.
- **The judge is slower than no-judge.** Every `external_facing` and `high_risk` action incurs a subagent spawn. For executor stages dominated by `read_only` and `reversible_write` actions this is negligible; for stages with many external operations (e.g., heavy `gh` API or `curl` use) it adds real wallclock time.
- **Rules drift.** The shipped `action-classification.yaml` is generic. Projects with their own dangerous commands (`make deploy-prod`, custom CLI tools) will need to add project-specific rules; until they do, those actions fall into the default class (`reversible_write`) and execute without judge review.
- **The judge cannot see future state.** It evaluates one action at a time against the current manifest. A sequence of individually-authorized actions that compose into an unauthorized outcome (e.g., creating three files that together expose a secret) is not caught by the judge — it is caught by the policy stage and the verifier.
- **Auto-escalation after 3 revision cycles is an upper bound, not a target.** If revision_cycles is consistently high across runs, that usually indicates a manifest clarity problem, not a judge problem.

## [0.3.0] — 2026-05-11

The dual-AI audit-handoff discipline. Built from the CivicCast `process/shared-audit-knowledge` PR (commit `bfc5a2a`) which formalized a pattern that had been proven across multiple sprints: an implementing AI runs a hostile 5-lens self-audit before push, a verifying AI runs a documented 10-section protocol after push, and both share an in-repo doc so neither re-derives the rules from scratch each session.

### Added

- `/audit-init` slash command. Scaffolds the three-artifact dual-AI audit infrastructure for a project: out-of-repo `<PROJECT>_AUDIT_GATE.md` and `<PROJECT>_AUDIT_PROTOCOL.md`, in-repo `<project>/docs/process/5-lens-self-audit.md` (lands via PR), plus per-agent wiring (Claude memory feedback file on the Claude side, runtime-equivalent project-context file on the second AI's side).
- `pipelines/roles/cross-agent-auditor.md` — role file for the verifying agent. Mandatory 10-section output (Verdict / Claim Verification Matrix / Durable Artifact Reads / Substantive Content Checks / Drift Matrix / Working Tree & Remote State / Unreported Catches / Open Caveats / Paste-Ready Directive / Recommended Next Action). Status-word rules. Runtime confidence separation. Failure handling.
- `pipelines/roles/implementer-pre-push.md` — role file for the implementing agent. Five lenses (Engineering / UX / Tests / Docs / QA). Artifact-state checklist. Post-push SHA-propagation step. Proof-anchor vs release-target distinction. Report format with mandatory 5-lens block.
- `pipelines/templates/audit-gate-template.md` — short gate template with `<PROJECT_NAME>`, `<IMPLEMENTER_AGENT>`, `<AUDITOR_AGENT>`, `<AUDIT_PROTOCOL_PATH>` placeholders.
- `pipelines/templates/audit-protocol-template.md` — long protocol template with 22 sections; section 22 (Known Drift Patterns) is the project's catalog that accumulates over time.
- `pipelines/templates/5-lens-self-audit-template.md` — in-repo shared doc template with generic artifact-state checklist; project-specific items accumulate as the auditor surfaces new drift patterns.
- `docs/audit-handoff-handbook.md` — operator reference. When to use, how the two halves interact, role-agent matrix, stacking with the pipeline, honest expectations of what the discipline reduces vs. what it doesn't.

### Why each new piece exists

- **Dual-AI separation of duties.** A single AI auditing its own work catches less drift than two AIs with separate context. The implementer reads its own diff as the agent that produced it; the auditor reads cold against a documented protocol. Second-perspective catches what first-perspective missed — the same property that makes human code review work.
- **5-lens self-audit before push.** A chat-promise ("I'll keep this in mind") is not a behavior change. The behavior change is the durable artifact: the hostile self-audit on the actual diff, with results printed in the report. Forces the implementing agent to rebut its own diff before pushing.
- **10-section verification output.** Sparse audits ("looks good to me") generate no useful directives for the next implementing turn. The 10-section structure forces the auditor to produce a paste-ready directive every turn, even when cleanup is complete (then it's the next-phase directive).
- **In-repo shared doc.** Both agents read it. When the auditor finds drift, the auditor's directive references the relevant section. New drift patterns get added as artifact-state checklist items — the discipline strengthens over time.
- **Out-of-repo gate and protocol.** They govern the auditor's behavior BEFORE entering the repo. They can be updated without dragging a PR through project review (when standards tighten mid-sprint).

### Role-agent symmetry

The discipline is symmetric. Any AI can play either role; the plugin runs in Claude Code, the second AI can be any tool that exposes a standing-instructions surface (project-context file, skill registration, custom-instruction field, etc.). `/audit-init` asks for role assignment and wires the per-agent pointers accordingly. Single-agent fallback is supported but loses the structural benefit.

### Stacking with v0.2

- The pipeline (v0.2) catches execution-cascade failures: pre-existing CI infrastructure bugs, tag-move dances, halt-and-ask loops.
- The audit-handoff discipline (v0.3) catches drift failures: wrong endpoint, stale CHANGELOG, "Closed" without evidence, status-word abuse, durable docs drifting in parallel.

The two stack. Pipeline's Phase 1 is where the implementer's 5-lens fires. Pipeline's Phase 4 is where the auditor's 10-section output fires. Human gates remain unchanged.

### Known limitations

- The discipline does not prevent wrong-direction product decisions (audit verifies execution, not strategy).
- It does not prevent cascading CI infrastructure bugs (that's what the pipeline's Phase 0 is for).
- Single-agent runs collapse to self-audit-only — the structural benefit comes from independent context.
- The drift-pattern catalog (section 22 of the protocol) starts empty for new projects. The first few audit cycles will surface patterns that establish the project's specific drift profile.

## [0.2.0] — 2026-05-11

The `module-release` pipeline. Built from the CivicSuite civicrecords-ai v1.5.0 sprint that burned ~8 hours on cascading discovery of three pre-existing latent `release.yml` bugs. Validated end-to-end against the CivicSuite D2/B3 staff_key_gate sprint, which shipped CivicCore v1.1.0 with **zero tag moves** on the first push — the pipeline's design target.

### Added

- `pipelines/module-release.yaml` — 4-phase pipeline: Phase 0 preflight → Phase 1 product → Phase 2 local rehearsal → Phase 3 remote release + umbrella → Phase 4 verifier → Phase 5 manager. Human gates at Phase 0 / Phase 2 / Phase 5.
- `pipelines/roles/preflight-auditor.md` — Phase 0 role. Audits the module's release workflow and supporting CI before any product code is touched. Check 1–7 sequence (YAML parse, workflow run health, scripts exist, local verify on fresh state, cross-platform reality, diagnostic instrumentation, audit-punchlist correlation). Bugs found are bundled into ONE PR.
- `pipelines/roles/local-rehearsal.md` — Phase 2 role. Mirrors the CI environment and runs the release sequence locally on fresh state before the tag push. The release workflow becomes the execution mechanism, not the discovery mechanism.
- `pipelines/self-classification-rules.md` — pre-authorized classifications applied during Phase 1: LIVE-STATE / FROZEN-EVIDENCE / SHAPE-GUARD / OWN-MODULE-VERSION for grep hits; MECHANICAL-CI-BUG / CONTRACT-CHANGE / ENVIRONMENTAL / NOVEL for failures. Bundling discipline and a tag-move budget (target 0, ceiling 1 per sprint).
- `scripts/preflight_infrastructure.py` — Phase 0 runner. Six automated checks; non-zero exit blocks Phase 1 work.
- `docs/module-release-handbook.md` — operator reference with honest timing expectations (`~8h → ~2-3h` for infra-debt modules) and a "what this pipeline does NOT prevent" section (unknown unknowns, inter-module integration surprises, agent judgment errors).

### Why each new piece exists

- **Phase 0 prevents the cascade.** The civicrecords-ai sweep had three latent `release.yml` bugs that surfaced one at a time during Phase 3 (remote release). Each surface required a PR + merge + tag-move + 4-minute CI cycle. Phase 0 inverts this: find the bugs by reading workflow YAML, grepping referenced scripts, running `verify-release.sh` locally on fresh state, and cross-referencing the audit punchlist. Fix all of them in ONE bundled PR. Then product work begins.
- **Self-classification rules prevent halt-and-ask churn.** The civicrecords-ai chat had ~25% of its content as "agent halted, asked permission, human answered, agent continued" on routine cases (URL update, version-string update, frozen-doc skip, shape-guard skip). The rules pre-authorize the long tail; halt-and-ask is reserved for genuine novelty.
- **Phase 2 prevents the tag-move dance.** The civicrecords-ai v1.5.0 tag moved FOUR times during recovery. Phase 2 forces the agent to run the release sequence locally before pushing the tag; the workflow becomes the execution mechanism, not the discovery mechanism.

### Validation

End-to-end run against CivicSuite D2/B3 staff_key_gate sprint (2026-05-11):
- Phase 0 found one infrastructure bug, bundled fix shipped as CivicCore PR #55.
- Phase 1 product work shipped staff_key_gate helper as CivicCore PR #56.
- Phase 2 local rehearsal passed on fresh state; SHA captured matched final release.
- Phase 3 tag push: **zero tag moves**, release published first try.
- Six downstream module sweeps merged green; umbrella PR #123 merged through `release-lockstep-gate`.
- All 26 modules PASS on `verify-suite-state.py --remote-only`.

### Known limitations

- Designed for module-version-bump and dependency-migration sprints. Not a fit for pure feature work (use `feature.yaml`) or bug fixes that don't ship a release (use `bugfix.yaml`).
- The Phase 2 local rehearsal cannot fully simulate signed/notarized Windows installer builds without a local Windows VM, or macOS notarization without paid Apple credentials. Document the trust gaps in the rehearsal report.
- Pipeline timing wins are concentrated in modules with infrastructure debt. Low-debt modules see modest improvement (~30 min sprint stays ~30 min).

## [0.1.0-beta] — 2026-05-09

Initial public beta. The plugin has shipped real features in at least
one project (CivicCast Sprint 0.3); the slash-command edge cases will
surface in your codebase before they surface in the maintainer's.

### Added

- `/pipeline-init` slash command with three onboarding paths:
  PRD/spec document, existing repo (URL or local path), or description
  paragraph. Scaffolds `.pipelines/`, `scripts/policy/`, `CLAUDE.md`,
  and a `.gitignore` entry.
- `/new-run` slash command. Initializes
  `.agent-runs/<run-id>/manifest.yaml` from the template.
- `/run-pipeline` orchestrator slash command. Reads the pipeline YAML,
  walks stages in order, dispatches to one of three handlers
  (human-gate / pipeline-command / agent), writes append-only
  `run.log`, resumes from the right place on re-invocation.
- `feature` pipeline (8 stages: manifest → research → plan →
  test-write → execute → policy → verify → manager).
- `bugfix` pipeline (7 stages: manifest → research → plan →
  reproduce → patch → policy → verify → manager).
- Six role files: researcher, planner, test-writer, executor, verifier,
  manager. Each is the verbatim contract a fresh subagent receives.
- Three policy checks shipped:
  `check_allowed_paths.py`, `check_no_todos.py`,
  `check_adr_gate.py`, plus the `run_all.py` aggregator.
- `manifest-template.yaml` with inline field documentation: `id`,
  `type`, `branch`, `goal`, `allowed_paths`, `forbidden_paths`,
  `non_goals`, `expected_outputs`, `required_gates`, `risk`,
  `rollback_plan`, `definition_of_done`, `director_notes`.
- Documentation: `README.md`, `USER-MANUAL.md` (operator-facing),
  `ARCHITECTURE.md` (with Mermaid diagrams), `docs/index.html`
  (GitHub Pages landing page).

### Lessons baked into defaults

- **Halts apply to ALL repo state changes.** No "obviously safe"
  cleanup PRs while a gate is open.
- **The manager must cite verifier evidence verbatim.** The role file
  forbids encouragement and summarization — these were how bad runs
  promoted in prior projects.
- **Policy checks halt the pipeline on non-zero exit.** No "warning
  only" — that's how scope creep gets in.
- **The `run.log` is append-only.** Editing it to "fix" a stage hides
  the underlying bug. The orchestrator parses the log to determine
  resume point; resume is the only valid recovery.
- **Subagents have fresh context.** Each stage starts with the role
  file + on-disk artifacts. The orchestrator's conversation history is
  not shared.
- **Cleanroom CI is recommended in the orientation summary.** A
  Docker-based reproduction with a fresh dependency set catches
  "works on my machine" bugs that local pytest misses.

### Known limitations

- The orchestrator does not currently support nested pipelines (one
  pipeline invoking another). Out of scope for v0.1.
- The plugin does not enforce that you have committed your manifest
  before starting a run; you can if you want, but `.agent-runs/` is
  gitignored by default.
- The `bugfix` pipeline assumes the bug is reproducible; if it isn't,
  the `reproduce` stage will fail and you'll need to fall back to a
  `feature`-style flow.
- Policy checks are written in Python; if your project doesn't have
  Python available, the policy stage will fail. (Roadmap item: a
  shell-only fallback.)

### Roadmap (not committed — feedback wanted)

- v0.2: optional `cleanroom` stage that runs the test suite in a Docker
  container with a fresh dependency install.
- v0.2: project-specific check templates (e.g.,
  `check_no_console_log.py` for JS projects, `check_ffmpeg_wrapper.py`
  for media projects).
- v0.3: a `refactor` pipeline type for behavior-preserving changes
  (different verifier criteria — diff-mode tests).
- v0.3: a `--dry-run` flag on `/run-pipeline` that walks the stage
  list and prints what would happen without spawning agents.

[0.3.0]: https://github.com/scottconverse/agent-pipeline-claude/releases/tag/v0.3.0
[0.2.0]: https://github.com/scottconverse/agent-pipeline-claude/releases/tag/v0.2.0
[0.1.0-beta]: https://github.com/scottconverse/agent-pipeline-claude/releases/tag/v0.1.0-beta
