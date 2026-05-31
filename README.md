# agent-pipeline-claude

**Ship multi-step Claude Code work that doesn't drift.**

The plugin reads your project's spec, drafts a per-run scope contract from it, and shows it to you in chat with a fast keyword gate: reply `APPROVE` to start, `REVISE` to send back, `VIEW` to print the full YAML. Then it runs research → plan → execute → verify → critique end-to-end with three chat-based human gates (deterministic first-token keyword parsing), an opt-in real-time judge, and machine-checkable auto-promote. Modal `AskUserQuestion` infrastructure is denied during active runs — the chat keeps full context visible at gate-decision time.

**Status:** Stable · v3.0.1 · Opus 4.8-native

> **Note on skills:** the plugin registers six skills — you do not hand-author any YAML. See [The skills](#the-skills) below.

**Current release: v3.0.1** — an audit-hardening release on top of the Opus-4.8-native v3.0.0. A full five-role audit of v3.0.0 (zero blockers) drove fixes across secret-redaction coverage, the destructive-command/secret trust boundary, manifest input-integrity, and release-doc hygiene. No behavior change for a healthy run. The full list is in the [CHANGELOG](CHANGELOG.md). [User Manual](USER-MANUAL.md) · [Architecture](ARCHITECTURE.md) · [Landing page](https://scottconverse.github.io/agent-pipeline-claude/) · [Discussions](https://github.com/scottconverse/agent-pipeline-claude/discussions)

## What it does (and doesn't)

**It does:**
- Draft a per-run scope contract from your project's existing spec/release-plan/design docs and gate it on a one-word chat `APPROVE`.
- Run a fixed multi-stage pipeline (research → plan → execute → policy → verify → drift-detect → critique → auto-promote → manager) with three human gates.
- Enforce the pipeline at runtime via eleven Cowork lifecycle hooks — deny destructive commands, block out-of-scope writes during a run, refuse invalid stops.
- Persist run state to disk (`.agent-runs/<run-id>/memory/`) so it survives context compaction, with an optional Mem0 layer for cross-session recall.
- Optionally supervise risky executor actions in real time with a context-isolated judge subagent.

**It doesn't:**
- Run fully autonomously. The three human gates are the minimum-viable structure by design; there is no grant-based autonomous mode (removed in v1.3.0).
- Auto-detect your Python launcher. Hooks invoke `python`; on macOS/most Linux you must provide a `python` shim or the hooks silently no-op (see [Troubleshooting](#troubleshooting)).
- Eliminate single-model-family blind spots. Context isolation between executor and judge reduces correlated errors; it does not remove them.
- Replace your tests, linter, or CI. It orchestrates around them.

## What's new in v3.0.1 (audit hardening)

A full five-role audit of v3.0.0 returned zero blockers; this release closes its findings. Highlights — see the [CHANGELOG](CHANGELOG.md) for the complete list:

- **Secret-redaction coverage (security).** The redaction layer now catches GitHub (`ghp_`/`github_pat_`), Slack, GitLab, Google, Stripe, npm, JWT, URL-embedded, and bare-credential secrets before they can reach the Mem0 layer. The prior pattern required a hyphen where GitHub uses an underscore and silently missed an entire class of tokens; the command-scanning hook and the config template move in lockstep.
- **Trust-boundary hardening.** The destructive-command and secret denylists normalize commands and route pipe-to-interpreter / `dd` / `mkfs` / `find -delete` / fork-bombs to the judge by default; the PreToolUse hook fails *closed* on an internal error.
- **Input integrity.** Manifest/scope-lock reads use a real YAML parser (a clear parse error instead of silent shape-coercion); `CLAUDE_PROJECT_DIR` is validated and the resolved root is surfaced in gate output.
- **Release hygiene.** Every doc surface, the manifest descriptions, and all script `--version` strings are synced to one version and guarded by a version-parity test. The two long-deprecated `*-autonomous` skills are removed.

## What's new in v3.0.0 (Opus 4.8-native)

- **Per-stage model/effort/speed binding.** Optional `model`/`effort`/`speed` hints on pipeline stages; quality-critical stages (verify/drift-detect/critique/manager + the manifest-drafter) bias to `effort: max`, and every model stage escalates to ≥ `effort: extra` under manifest `risk: high`. No model id is hardcoded.
- **Window-gated lean run-context.** On a 1M-context model, the orchestrator injects the manifest plus a read-on-demand artifact index instead of pasting every prior artifact inline. The critic always gets the full block (its hostile cold read must not be starved).
- **Parallel independent stages.** The independent verify/drift-detect/critique trio fans out concurrently.
- **Context-exhaustion early warning.** Non-blocking nudge at 60/70/80% of the detected context window.
- **SHA-256 per-run memory integrity.** Memory files carry a SHA-256 sidecar verified on reload; a tampered/corrupted file is detected, not silently trusted.
- **Inbound Mem0 scrub + framing.** Mem0 search results are scrubbed and framed on the read path; body-less records are dropped.
- **`allowed_paths` traversal hardening (security).** Write targets are lexically resolved (`os.path.normpath`, TOCTOU-safe), rejecting `..`-escapes above the repo root. The prior string-prefix check let `src/../../etc/passwd` through.

(WS-5, mid-conversation `role:system` messages, is deferred to a later release; the judge layer was WS-9, shipped in v2.3.0.)

## What's new in v2.3.0 (judge layer reincorporation)

Brings back the v0.4 judge layer as a platform-executable design: opt-in, real-time, context-isolated supervision of the executor's risky actions. Active only when `.pipelines/action-classification.yaml` exists; with no such file the executor stage runs exactly as before. The judge evaluates risky actions *before* they execute, in isolation from the executor's reasoning, realized at the orchestrator's altitude across subagent spawns. See `ARCHITECTURE.md` §7 for the trust-boundary model.

## What's new in v2.2.x (chat-gate restoration + auto-update awareness)

v2.2.1 reversed the v1.3.0 → v2.1.0 modal-gate experiment: Cowork's modal overlay hid the chat context the operator needed at gate-decision time. Gates are now chat-based with deterministic first-token keyword parsing, and the modal-budget hook **denies every `AskUserQuestion` during an active non-drafting run**. v2.2.2 added the SessionStart marketplace-update warning (see [Upgrading](#upgrading-from-a-prior-version)).

> Full version history — including the v2.0 eleven-hook enforcement layer, persistent memory, directive contracts, and Mem0 — is in the [CHANGELOG](CHANGELOG.md).

---

## What a typical run looks like

You're in your project. The plugin is installed. You type:

```
/agent-pipeline-claude:run "close QA-005 conflict-409 race"
```

> **Why the namespace prefix?** Per the [official Claude Code plugin docs](https://code.claude.com/docs/en/plugins), plugin skills are always invoked as `/<plugin-name>:<skill-name>` to prevent collisions across plugins. The bare `/run` form is reserved for standalone `.claude/commands/` files, not marketplace plugins.

Claude reads your project's spec / release plan / scope-lock / design notes, drafts a manifest, and pastes it in chat with a one-line provenance summary, then prints the manifest gate:

```
Drafted from docs/releases/v0.4-scope-lock.md §1 + docs/research/v04-slice1-design.md.
8/11 fields auto-derived, 3 need your confirmation.

=== Manifest gate ===
Manifest drafted at .agent-runs/<run_id>/manifest.yaml.

Reply with one word (case-insensitive):
  APPROVE  — start the run; spawn the researcher next
  REVISE   — stop; you'll describe what to change in the next message
  VIEW     — print the complete manifest.yaml to chat, then re-ask
```

You reply `APPROVE` (or `REVISE`, or `VIEW`). The pipeline runs. Three human gates along the way (manifest, plan, manager-decision), each a chat prompt with the keyword grammar. The last one auto-fires when the six machine-checkable conditions pass — no chat prompt at all. Final result lands in `.agent-runs/<run-id>/` as a structured paper trail.

## The skills

Six skills, all namespaced `/agent-pipeline-claude:<skill>`:

| Invocation | Purpose |
| :--- | :--- |
| `/agent-pipeline-claude:run "<short description>"` | Start a new run. Drafts the manifest, gates on `APPROVE`, orchestrates end-to-end. Also accepts `resume <run-id>` and `status`. |
| `/agent-pipeline-claude:pipeline-init` | Onboard a project. Inspects what's there, scaffolds `.pipelines/`, `scripts/policy/`, and a starter `CLAUDE.md`. |
| `/agent-pipeline-claude:audit-init` | Scaffold dual-AI audit-handoff infrastructure for projects where one AI implements and another audits. |
| `/agent-pipeline-claude:intake` | Capture a plain-English description and draft starter artifacts (`intake.md`, `manifest.yaml`, `scope-lock.yaml`) without starting the pipeline. Soft onboarding for ideas without a manifest. |
| `/agent-pipeline-claude:mem0 <subcommand>` | Manage the Mem0 cross-session memory layer (`init` / `up` / `down` / `whoami` / `test` / `sync` / `prune`). OSS-default; Platform mode behind explicit consent. |
| `/agent-pipeline-claude:show-run-status` | Read-only summary of a run's `.agent-runs/<run-id>/` state without resuming or mutating it. |

> The `run-autonomous` and `grant-autonomous` skills were deprecated in v1.3.0 and **removed in v3.0.1** — there is no separate autonomous mode; `/agent-pipeline-claude:run` is the single entry point.

## Why this plugin exists

Agentic work fails in predictable ways:

- The agent doesn't understand the project's conventions, so it improvises and the work silently diverges from the spec.
- The agent claims tests pass without running them against a fresh dependency set.
- The agent merges in-flight work while a scope question is open.
- The agent picks architectural decisions silently rather than surfacing them.
- The manifest the agent's working from doesn't match what the human actually wanted.

The plugin enforces a structural pattern that catches every one of those:

1. **Drafted scope contract.** The manifest is drafted from your project's existing docs and presented for chat `APPROVE`. You review what the agent thinks the run is; you don't author it from blank.
2. **Plan gate.** The planner produces a plan; you approve or send back.
3. **Policy stage.** Automated checks block the run if the manifest fails schema validation, any change falls outside `allowed_paths`, the diff contains TODO/FIXME/HACK markers, or an existing ADR was modified.
4. **Verifier stage.** Independent fresh-context check against every manifest exit criterion.
5. **Drift-detector + critic stages.** Adversarial cold-read of every artifact across six lenses; comparison of assembled state against the manifest contract.
6. **Judge layer (opt-in).** Real-time action-level supervision inside the executor stage. Risky tool calls spawn an independent judge subagent that allows / blocks / revises / escalates.
7. **Auto-promote.** Six conditions checked from the artifact stack: verifier-clean, critic-clean, drift-clean, policy-passed, judge-clean, tests-passed. When all six pass, the manager gate auto-fires; otherwise the human gate remains.

## Install

The plugin works in **Cowork** (the chat-first Claude Code surface) and in **Claude Code CLI**.

### Recommended: marketplace install

```
/plugin marketplace add scottconverse/agent-pipeline-claude
/plugin install agent-pipeline-claude@agent-pipeline-claude
```

Then restart your Cowork session (fully Quit and reopen — plugin metadata loads at app startup) or run `/reload-plugins` in the CLI.

### Alternative: file-level install (Cowork without marketplace UI)

Paste this prompt into any Claude session:

```
Install the agent-pipeline-claude plugin for me.

Method: clone https://github.com/scottconverse/agent-pipeline-claude
into ~/.claude/plugins/marketplaces/agent-pipeline-claude. Add an
agent-pipeline-claude marketplace entry to ~/.claude/plugins/known_marketplaces.json
pointing at that path. Add agent-pipeline-claude@agent-pipeline-claude to
~/.claude/plugins/installed_plugins.json with the cloned commit SHA.
In ~/.claude/settings.json, set
enabledPlugins["agent-pipeline-claude@agent-pipeline-claude"] = true and add
the marketplace to extraKnownMarketplaces.

Back up settings.json + known_marketplaces.json + installed_plugins.json
before patching. After install, fully quit Cowork (or restart your CLI session)
to load the new skills.
```

**Then fully restart Cowork.** After restart, `/agent-pipeline-claude:pipeline-init` and `/agent-pipeline-claude:run` are available.

### Local development / testing

```
claude --plugin-dir /path/to/agent-pipeline-claude
```

Loads the plugin for one session without installing.

**Requirements:** the Python enforcement gates and Cowork hooks need **PyYAML** on **Python ≥ 3.10**; install with `pip install -r requirements.txt`. Everything else is the standard library. The optional Mem0 layer is installed separately (`pip install mem0ai`).

## First use in a new project

Drop into the project root and run:

```
/agent-pipeline-claude:pipeline-init
```

The plugin inspects what your project has — spec, release plan, CLAUDE.md, tests, CI workflows — produces a one-message orientation summary, and asks you to `APPROVE` before scaffolding `.pipelines/`, `scripts/policy/`, role files, and a starter `CLAUDE.md` (only if you don't already have one). Edit the `## Pipeline drafter notes` section of that `CLAUDE.md` before your first `/agent-pipeline-claude:run` for best results.

## Running a pipeline

```
/agent-pipeline-claude:run "short description of the work"
```

Reply `APPROVE` (one word, case-insensitive) to start, `REVISE` followed by changes to send back to the drafter, or `VIEW` to print the full manifest first.

### Other shapes

```
/agent-pipeline-claude:run resume 2026-05-12-my-task-slug   # pick up a halted run
/agent-pipeline-claude:run status                            # list runs in this project
/agent-pipeline-claude:run                                   # same as `status`
```

## The three human gates

Each fires as a chat prompt with a deterministic keyword grammar. The orchestrator parses the first non-whitespace token of your next message, case-insensitive: `APPROVE` to accept, `REVISE`/`REPLAN` to send back, `BLOCK` to halt, `VIEW` to print the underlying artifact and re-ask. Anything else re-prints the prompt with a no-parse note.

1. **Manifest gate.** Review the drafted scope contract; `APPROVE` to start, `REVISE` to send back (max 5 cycles), `VIEW` to print first.
2. **Plan gate.** After research → plan; `APPROVE` to execute, `REPLAN` with revisions, `BLOCK` to halt, `VIEW` to print.
3. **Manager gate.** After everything else; the manager produces a PROMOTE/BLOCK/REPLAN recommendation citing verifier, drift-detector, and critic findings verbatim. `APPROVE`, `BLOCK`, `REPLAN`, or `VIEW`.

When auto-promote's six conditions all pass, the manager gate skips entirely (no chat prompt) and the run reports DONE-PROMOTED.

> **The `pipeline-init` orientation gate uses a different keyword set.** It is a *setup* gate, not a pipeline-run gate: reply `APPROVE` to scaffold, `WAIT` to hold, or `CANCEL` to abort. The five-keyword grammar above applies to the three pipeline-run gates, which are a distinct gate type.

## What about specs and release plans?

The drafter reads these patterns at the project root (or under `docs/`):

| Category | Filename patterns it walks |
| :--- | :--- |
| Project spec | `*UnifiedSpec*.md`, `SPEC.md`, `PRD.md`, `REQUIREMENTS.md`, `docs/spec/*.md` |
| Release ladder | `*ReleasePlan*.md`, `RELEASE-PLAN.md`, `ROADMAP.md`, `docs/spec/release-plan.md` |
| Per-rung scope contract | `docs/releases/v*-scope-lock.md`, `docs/releases/<rung>-scope.md` |
| Design notes | `docs/research/<version>-<feature>-design.md`, `docs/design/*.md` |
| ADRs | `docs/adr/*.md` (closed architectural decisions) |
| Conventions | `CLAUDE.md` at root |
| Findings | `audit-*/`, `findings/*.md`, `next-cleanup.md` |

If your project has none of these, the drafter falls back to greenfield mode: it asks you to paste a 1–3 paragraph description and synthesizes a minimal spec + draft from it. You can also point the drafter at your layout in `CLAUDE.md` under a `## Pipeline drafter notes` section (the `pipeline-init` scaffolder writes that section for you).

## Plugin layout

```
.claude-plugin/
├── plugin.json              # plugin manifest
└── marketplace.json         # marketplace manifest (validates with `claude plugin validate .`)
skills/                      # six skills, each self-contained in its own folder
├── run/                     #   SKILL.md (thin shim) + references/run.md (canonical procedure)
├── pipeline-init/
├── audit-init/
├── intake/
├── mem0/
└── show-run-status/
hooks/                       # hook_runner.py + hook_utils.py + hooks.json (11 lifecycle events)
memory/                      # Mem0 layer: config, redaction, identity, policy, adapter
pipelines/                   # shared pipeline definitions copied into projects by pipeline-init
scripts/                     # policy/enforcement gates + check_skill_packaging.py
schemas/                     # JSON schemas (mem0 config, etc.)
tests/                       # check_plugin_structure.py + unit/cleanroom tests + fixtures
```

Each skill is **self-contained** in its own folder — SKILL.md only references files inside `references/`, never repo-root files. This is enforced by `scripts/check_skill_packaging.py`, which simulates the plugin loader copying just `skills/<name>/` into a temp directory and verifies every backtick-quoted `references/...` path resolves.

## Where a run's artifacts live

```
.agent-runs/<run-id>/
├── manifest.yaml              # the run's scope contract (drafted, then APPROVE'd)
├── draft-provenance.md        # which manifest fields came from which sources
├── research.md · plan.md      # researcher findings · planner plan (after human APPROVE)
├── implementation-report.md   # executor's output
├── policy-report.md           # auto-policy checks results
├── verifier-report.md · drift-report.md · critic-report.md
├── auto-promote-report.md     # six-condition check (only when NOT_ELIGIBLE)
├── manager-decision.md        # final PROMOTE/BLOCK/REPLAN
├── judge-log.yaml · judge-metrics.yaml · judge-decisions/<action_id>.yaml  # when judge active
├── memory/                    # durable run memory (survives compaction); SHA-256 integrity-checked
└── run.log                    # chronological STAGE_DONE / STAGE_FAILED entries
```

## Other pipelines

- **Module release** — for work whose end-state is a published artifact, `/agent-pipeline-claude:run "v1.2.0 release"` selects the six-phase `module-release` pipeline (Phase 0 preflight → scoped product work → local rehearsal → remote release → verifier → manager). See `docs/module-release-handbook.md`.
- **Dual-AI audit-handoff** — for projects where one AI implements and another audits, `/agent-pipeline-claude:audit-init` scaffolds the shared discipline (in-repo 5-lens self-audit + out-of-repo audit gate + protocol). See `docs/audit-handoff-handbook.md`.

## Troubleshooting

**Problem:** `/agent-pipeline-claude:run` returns "Unknown command."
**Likely cause:** plugin not loaded, or client not restarted after install.
**Fix:** `claude plugin list` should show `✔ enabled`. Fully quit and reopen Cowork (a new conversation is not enough). Confirm `installed_plugins.json` points at the clone you have.

**Problem:** Skills don't appear in the palette.
**Fix (in order):** (1) fully quit/restart the client; (2) check `enabledPlugins["agent-pipeline-claude@agent-pipeline-claude"]: true` in `settings.json`; (3) confirm the clone exists and contains `skills/run/SKILL.md`; (4) run `claude plugin validate ~/.claude/plugins/marketplaces/agent-pipeline-claude`; (5) use the namespaced form `/agent-pipeline-claude:run`, never bare `/run`.

**Problem:** Hooks silently fail on macOS/Linux.
**Likely cause:** `hooks/hooks.json` invokes `python`, but your system only has `python3`.
**Fix:** create a `python` shim on PATH (`ln -s "$(which python3)" ~/.local/bin/python`), or on Debian/Ubuntu `apt install python-is-python3`.

**Problem:** `auto-promote` reports NOT_ELIGIBLE.
**Fix:** read `.agent-runs/<run-id>/auto-promote-report.md`; it cites the failing condition (critic findings > 0, verifier open items > 0, tests didn't run, judge blocked an action). Address it and re-run.

## Upgrading from a prior version

**Third-party Claude Code marketplaces have auto-update OFF by default.** Per the [official docs](https://code.claude.com/docs/en/discover-plugins#configure-auto-updates):

> Official Anthropic marketplaces have auto-update enabled by default. **Third-party and local development marketplaces have auto-update disabled by default.**

The `agent-pipeline-claude` marketplace is third-party. After each release, do ONE of these to actually receive the new version:

**Option 1 — explicit install (recommended for a one-time upgrade):**

```bash
# Refresh the marketplace clone:
cd ~/.claude/plugins/marketplaces/agent-pipeline-claude
git pull
git checkout v3.0.1

# Install the new version into the cache:
claude plugin install agent-pipeline-claude@agent-pipeline-claude
```

Then `/reload-plugins` in any Cowork session (or restart Cowork) to load the new hooks.

**Option 2 — enable auto-update once, ride future releases hands-free:**

Run `/plugin` in Cowork → **Marketplaces** tab → select `agent-pipeline-claude` → **Enable auto-update**. Then restart Cowork.

**Why this matters:** since v2.2.2 the SessionStart hook detects when the marketplace clone is ahead of the installed `gitCommitSha` and emits a loud warning at the top of the session context with the exact `claude plugin install` command — so even if you forget, you get an in-session reminder instead of silently running a stale version. If you're coming from a pre-v1.0 line, the two-step `/new-run` + `/run-pipeline` flow is long gone — use `/agent-pipeline-claude:run`. See [CHANGELOG.md](CHANGELOG.md) for full migration notes.

## Documentation

- [User Manual](USER-MANUAL.md) — operator-facing step-by-step reference
- [Architecture](ARCHITECTURE.md) — diagrams + design rationale
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Module Release Handbook](docs/module-release-handbook.md) · [Audit-Handoff Handbook](docs/audit-handoff-handbook.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
