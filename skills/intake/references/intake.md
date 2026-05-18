---
description: Capture a plain-English task description and draft pipeline run artifacts without starting work.
argument-hint: [pipeline-type] [slug] <description>
---

# intake - draft a pipeline run from a plain-English request

You are creating draft starting artifacts for a future pipeline run. This is a
soft onboarding doorway when the user has a product, repo, design, task, bug, or
feature in mind but does not yet have a manifest.

Do not start the pipeline. Do not validate the manifest. Do not spawn agents.
Do not run tests or policy checks. Draft artifacts only.

## Prerequisite

The project must already be initialized with `pipeline-init`. Verify these files
exist:

- `.pipelines/manifest-template.yaml`
- `.pipelines/scope-lock-template.yaml`
- at least one executable pipeline YAML such as `.pipelines/feature.yaml`

If any are missing, stop and tell the user to run
`agent-pipeline-claude:pipeline-init` first. Do not create ad hoc pipeline files.

## Inputs

Use the user's message as `$ARGUMENTS`.

Accepted forms:

- `feature account-deletion Add account deletion to settings...`
- `bugfix login-timeout Fix users being logged out after refresh...`
- `Add account deletion to settings...`

If the prompt does not include a usable description, ask exactly one question and
then stop:

> Describe the product, repo, design, task, bug, or feature you want the
> pipeline to work on. Include important constraints, likely target files,
> success criteria, and what should not change.

## Inference rules

Infer conservatively:

- `pipeline_type`: use an explicit first token when it matches `.pipelines/<type>.yaml`.
  Otherwise choose `bugfix` only for clear bug/fix/regression language; choose
  `module-release` only for explicit release/version/migration work; default to
  `feature`.
- `slug`: use an explicit second token when it is lowercase ASCII kebab-case.
  Otherwise derive a short kebab-case slug from the description, max 8 words.
- `run_id`: `YYYY-MM-DD-<slug>` from the system date.

If the selected `.pipelines/<pipeline_type>.yaml` does not exist, list available
pipeline YAMLs and stop.

If `.agent-runs/<run_id>/manifest.yaml`, `scope-lock.yaml`, or `intake.md`
already exists, stop and report the existing run path. Do not overwrite.

## Artifact contents

Create `.agent-runs/<run_id>/`.

### 1. intake.md

Write `.agent-runs/<run_id>/intake.md` with:

```markdown
# Intake: <run_id>

status: draft
pipeline_type: <pipeline_type>
slug: <slug>
created_from: agent-pipeline-claude:intake

## Source description

<verbatim user description>

## Conservative interpretation

- User-facing goal: <one-sentence interpretation, or TODO if uncertain>
- Likely allowed paths: TODO - fill before running the pipeline
- Likely forbidden paths: docs/adr/ unless creating a new ADR; TODO - refine
- Expected outputs: TODO - list testable artifacts and behaviors
- Non-goals: TODO - list explicit out-of-scope work
- Risk guess: low | medium | high

## Missing information before running the pipeline

- Confirm the exact allowed_paths.
- Confirm expected_outputs as testable criteria.
- Confirm rollback_plan.
- Confirm definition_of_done.
- Fill scope-lock.yaml from the canonical release plan or project authority.

## Next steps

1. Review and complete manifest.yaml.
2. Review and complete scope-lock.yaml.
3. Run `agent-pipeline-claude:run resume <run_id>` to start the pipeline.
   The pipeline's manifest gate validates the manifest before any agent runs.
```

### 2. manifest.yaml

Start from `.pipelines/manifest-template.yaml`. Preserve comments and field
order. Replace:

- `id: ""` with `id: "<run_id>"`
- `type: feature` with `type: <pipeline_type>`
- `goal: ""` with a quoted draft goal derived from the description and prefixed
  with `DRAFT - review:`
- `branch: ""` with a conservative branch suggestion such as
  `<pipeline_type>/<slug>`
- `rollback_plan: ""` with `TODO - define before running the pipeline`
- `definition_of_done: ""` with `TODO - define testable completion criteria before running the pipeline`

Leave list fields as empty lists unless the user explicitly supplied concrete,
safe values. Do not invent allowed paths.

### 3. scope-lock.yaml

Copy `.pipelines/scope-lock-template.yaml` and make only conservative draft
replacements:

- `current_rung: ""` -> `current_rung: "TODO - fill from canonical source"`
- `rung_title: ""` -> `rung_title: "TODO - fill from canonical source"`
- `proves: ""` -> `proves: "TODO - fill from canonical source"`

Do not invent canonical release-plan facts.

### 4. intake-questions.md

If required information is missing, write
`.agent-runs/<run_id>/intake-questions.md` listing the missing answers. This is
expected for most intakes.

### 5. active-control-state.md (Pass 12 — bridge model)

Write `.agent-runs/<run_id>/active-control-state.md` with the drafting bridge
state so the hook layer surfaces this run in session-context but does NOT
auto-deny scope violations against a manifest the operator is still drafting:

```markdown
active_run: drafting
current_stage: intake_drafted
next_required_action: Complete the TODOs in manifest.yaml + scope-lock.yaml, then run /agent-pipeline-claude:run resume <run_id>.
continuing_to: pipeline_start
stop_condition: awaiting_operator_completion
final_response_allowed: true
```

`active_run: drafting` is the bridge state — `discover_active_runs()` returns
this run with `is_drafting=True`, the session-context line labels it DRAFTING,
and `permission_decision()` downgrades scope-lock / allowed_paths denies to
advisory warnings. Absolute reasons (destructive command, credential exposure)
still deny.

When the operator runs `/agent-pipeline-claude:run resume <run_id>`, the run
skill flips `active_run` to `true` and the full enforcement set lights up.

## Final response

Show the paths created (including `active-control-state.md`), summarize that
they are drafts, name the bridge state (`active_run: drafting` — hooks
surface this run as advisory but won't auto-deny on scope violations), and
tell the user the next action is to complete the TODO fields and then run
`agent-pipeline-claude:run resume <run_id>` to promote the run to full
enforcement (`active_run: true`).

Do not say the run is ready. Do not start the pipeline.

## Hard rules

- Drafting is not approval.
- Never auto-approve the manifest.
- Never auto-create a directive contract.
- Never start research, planning, tests, execution, policy, verification, or manager stages.
- Never write outside `.agent-runs/<run_id>/`.
- If uncertain, write a TODO or an intake question instead of inventing authority.
