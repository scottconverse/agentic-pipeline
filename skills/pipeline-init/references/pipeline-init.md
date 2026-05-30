# Pipeline-init procedure — onboard a project

You are onboarding a project for use with the agent-pipeline-claude plugin. Most projects only need to run this once.

The plugin needs three things to do useful work later:
1. A `.pipelines/` directory with role files + pipeline definitions.
2. A `scripts/policy/` directory with the validation scripts.
3. A `CLAUDE.md` capturing the project's conventions (the manifest-drafter reads it).

This command produces all three, drafted from whatever the project already has.

## Argument shapes

`$ARGUMENTS` is one of:

1. **Empty** — the common case. You're standing in the project root; this command inspects what's there.
2. **A file path** — points at a PRD, spec, or description document. Read it as the source of truth for project orientation.
3. **A URL** — a repo URL. The current working directory must be empty; this command will `git clone` then init.
4. **A description paragraph** quoted at the prompt. Treat as inline-content PRD.

## What to do

### Step 1 — orient

Detect the project's current state:

```bash
git status            # Are we in a repo? Clean tree?
ls                    # What's at the root?
git log --oneline -5  # Recent commits — gives context on the project's life
```

Look for spec / release-plan / scope-lock / design-note artifacts using the same patterns the manifest-drafter walks (see `references/pipeline-payload/pipelines/roles/manifest-drafter.md` § "Source-walking protocol"). Look for stack indicators: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc. Look for `.github/workflows/`, `docs/adr/`, `CLAUDE.md`.

### Step 2 — produce a one-message orientation summary

Send the user a single chat message with:

```
Project orientation:

  Name: <inferred>
  Stack: <language, framework, test runner, lint, type checker>
  Existing artifacts: <list of relevant docs found, e.g. "CivicCastUnifiedSpec-v2.md, CivicCast-ReleasePlan, docs/releases/v0.4-scope-lock.md, CLAUDE.md">
  Missing: <gaps, e.g. "no docs/adr/ — the ADR policy gate will be disabled until you add one">
  Test framework: <pytest | jest | unknown>
  CI: <detected workflow files or "none">
```

The summary is informational. **Then print the chat gate prompt** at the end of your reply naming the recognized keywords. Stop and wait for the operator's next message; parse the first non-whitespace token, case-insensitive.

```
=== Scaffold gate ===
Scaffold .pipelines/, scripts/policy/, and (if missing) a starter CLAUDE.md?

Reply with one word (case-insensitive):
  APPROVE  — create .pipelines/, scripts/policy/, .gitignore entry, and starter CLAUDE.md if missing.
  WAIT     — pause so you can fix something in the orientation summary first.
  ADJUST   — describe corrections to the summary in your next message; the summary re-renders and the gate re-prompts.
  CANCEL   — stop without scaffolding.
```

Anything unrecognized re-prints the gate prompt with a no-parse note. Do NOT scaffold without an `APPROVE` reply from the operator.

**Why chat, not modal:** v2.2.1 removed the `AskUserQuestion` modal infrastructure from gates (see CHANGELOG v2.2.1). The Cowork modal overlay hid the chat context the operator needed to read at gate-decision time, defeating the gate's purpose. v1.3.0 → v2.1.0 used modals for the run skill's three gates plus pipeline-init's orientation gate; v2.2.1 reverses all of them to chat with deterministic first-token keyword parsing. The interpretive-surface concern that drove the modal redesign is structurally addressed by the explicit keyword grammar in each gate prompt and the no-parse re-print branch.

### Step 3 — scaffold on APPROVE

When the operator replies APPROVE at the orientation gate:

**Source of truth for the scaffolded files:** the bundled payload at
`references/pipeline-payload/` inside this skill (resolved relative to the
skill's install directory — `skills/pipeline-init/`). The payload ships INSIDE
the skill so it's always available, including when the plugin runs from an
installed cache where the repo-root `pipelines/` and `scripts/` paths don't
exist.

1. **`.pipelines/` directory.** Copy from `references/pipeline-payload/pipelines/` into the project root as `.pipelines/`:
   - `feature.yaml`
   - `bugfix.yaml`
   - `module-release.yaml` (if user wants module-release support; default yes for projects with version files)
   - `manifest-template.yaml`
   - `self-classification-rules.md`
   - `roles/` (all role files, including `manifest-drafter.md`)
   - `templates/` (the audit-handoff templates)

2. **`scripts/policy/` directory.** Copy from `references/pipeline-payload/scripts/` into the project root as `scripts/policy/`:
   - `__init__.py`
   - `check_manifest_schema.py`
   - `check_allowed_paths.py`
   - `check_no_todos.py`
   - `check_adr_gate.py`
   - `auto_promote.py`
   - `run_all.py`

3. **`.gitignore`** — append `.agent-runs/` if not already present.

4. **`CLAUDE.md`** — if the project doesn't have one, scaffold a starter. The starter is short (no boilerplate) and includes ONLY:
   - One paragraph: what this project is, derived from Step 2 orientation.
   - `## Pipeline drafter notes` section — tells the manifest-drafter where this project keeps its spec, release plan, scope-locks, design notes, ledgers, and HANDOFF. This is the file's most important section for v1.0 operation.
   - `## Order of operations` — three sentences on how changes flow (e.g. "branch from main, work in slices, tag at rung close").
   - `## Tooling` — language, test runner, lint, type checker, pre-commit hooks.
   - `## Non-negotiables` — empty placeholder for the user to fill in.
   - **(v2.1.0; updated v2.2.1) `## Memory precedence during pipeline runs`** — fixed boilerplate block stating: *"During active agent-pipeline-claude runs, the pipeline's chat gate keywords (`APPROVE` / `REVISE` / `REPLAN` / `BLOCK` / `VIEW`) and hook policy are authoritative. Operator-layer memory rules about asking-before-deciding (e.g. `feedback_no_unilateral_product_decisions.md`) apply OUTSIDE pipeline runs only. Inside a run, the v2.2.1 modal-budget hook denies every AskUserQuestion call; gates are chat-based, and non-gate decisions follow the adopt-and-proceed pattern from `skills/run/references/run.md`."* This boilerplate makes the precedence explicit at every project root, since memory files load globally and operators may not realize their broad rules conflict with the pipeline's autonomy contract.

   The user is expected to edit it. The plugin gives a starting shape, not the final word.

5. **(v2.1.0) `SPEC.md` project-shape field** — when scaffolding a greenfield SPEC.md (greenfield handling section below), include a `project_shape: <variant>` field near the top. Recognized values:
   - `single-codebase` (default; existing rung-versioned project)
   - `multi-repo-admin` (orchestration root with per-target-repo work clones under `_repos/`; non-numeric rung names allowed)
   - `library` (single repo without a rung-versioned release plan; SPEC.md is canonical rung-equivalent)

   Policy scripts (`check_allowed_paths`, `check_scope_lock`) read this field and branch their logic. Without it, they default to `single-codebase` (back-compat).

6. **Final scaffold report.** Send a chat message:
   ```
   Scaffold complete.

   Created:
     .pipelines/  (<N> role files, <M> pipeline definitions)
     scripts/policy/  (<K> validation scripts)
     CLAUDE.md  (starter — edit before your first run)
     .gitignore  updated

   Missing pieces (you can fix any time):
     - No docs/adr/ — ADR policy gate disabled until first ADR
     - No tests/ directory — test-tracking will be approximate

   Next step:
     /run "short description of your first run"

   IMPORTANT: if you just installed the plugin via the file-level
   install (clone + JSON patch), restart Cowork before /run becomes
   available in the slash-command palette.
   ```

### Step 4 — the Cowork install reality

The user may be running `/pipeline-init` right after a fresh install. Two scenarios:

**Scenario A — they used the file-level install (Cowork).** They cloned the repo and patched JSON files (or you/Claude did it for them). The slash commands register at session start, so the user is reading this in a fresh Cowork session AFTER restart. Everything works.

**Scenario B — they used `/plugin install` (CLI with that command available).** Same outcome — the slash commands are available.

If `/pipeline-init` itself doesn't appear available, the user can't be reading these instructions. So Scenario A/B is the universe; this command only runs once the plugin is loaded.

What the command DOES need to flag, at end of Step 3: if the user then runs `/run` and gets "command not available," they should restart Cowork. The scaffold report's final paragraph names this.

## Hard rules

- Never overwrite an existing `CLAUDE.md`. If it exists, render an informational chat message naming the file's contents, then print a chat gate prompt with `APPEND` / `SKIP` as recognized keywords (case-insensitive; first non-whitespace token of operator's next message).
- Never overwrite an existing `.pipelines/` directory. If it exists, treat as re-init: render the summary in chat and print the subset-to-refresh chat gate (see "Re-init handling" below).
- Never copy any file outside the project root the user is in.
- Never read or modify the plugin's own marketplace dir under `~/.claude/plugins/marketplaces/`.
- Always produce an orientation summary BEFORE the chat gate prompt fires. Show your reading in chat first, then print the gate.

## Greenfield handling

If `$ARGUMENTS` is a description paragraph (no spec file exists, no repo to read), synthesize a minimal spec inline and render it in chat:

```
You gave me a description but no existing spec. Synthesizing a minimal
spec now — review the draft below and reply at the gate prompt to
choose how to proceed.

[synthesized minimal spec: 1-2 paragraphs of purpose, target audience,
core capabilities, tech-stack inferences, license]
```

Then print the chat gate prompt:

```
=== Write SPEC gate ===
Write the synthesized SPEC.md to disk?

Reply with one word (case-insensitive):
  APPROVE  — save the draft above as SPEC.md at the project root.
  WAIT     — don't write yet; you'll edit the draft inline first.
  CANCEL   — abandon the synthesis.
```

Stop and wait for the operator's next message. Parse the first non-whitespace token, case-insensitive. Anything unrecognized re-prints the gate prompt with a no-parse note. Once `APPROVE` is parsed, write `SPEC.md` at project root and continue to Step 2 with `SPEC.md` as the read source.

## Re-init handling

If `.pipelines/` already exists, the project was initialized before. Render the situation in chat:

```
Project is already initialized (.pipelines/ exists with <N> files).
```

Then print the chat gate prompt for the refresh subset:

```
=== Re-init gate ===
Refresh what? (.pipelines/ already exists)

Reply with one word (case-insensitive):
  ROLES        — refresh role files from the current plugin version. Useful after upgrading the plugin.
  POLICY       — refresh scripts/policy/ only.
  EVERYTHING   — refresh role files + policy scripts + manifest template.
  CANCEL       — leave the existing setup as-is.
```

Stop and wait for the operator's next message. Parse the first non-whitespace token, case-insensitive. Apply the selected option; do NOT touch the user's `.agent-runs/`, manifests, or CLAUDE.md without an explicit second `APPROVE` chat gate.

**Why chat, not modal:** v2.2.1 reverses the v1.3.0 → v2.1.0 modal-gate design after the operator-UX failure where Cowork's modal overlay hid chat context at gate-decision time. The interpretive-surface concern from the original v0.5.x free-form chat ceremony is structurally addressed in v2.2.1 by the explicit keyword grammar in each prompt and the no-parse re-print branch.
