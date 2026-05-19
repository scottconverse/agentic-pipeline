# Run procedure — drafted-and-driven pipeline run (v1.3.0)

You are the single entry point for a pipeline run. Replaces v1.2.x's run + run-autonomous + grant-autonomous trio.

You do NOT do the work of any stage yourself. You drive the orchestrator and the user-facing gate surface. Stage work is delegated to subagents via the `Agent` tool (or to policy scripts via Bash). Your job is the loop, the human gates, and the run-log.

## Argument shapes

`$ARGUMENTS` is one of:

1. **A short description** of the run — e.g. `"close QA-005 conflict-409 race"`, `"slice 1 commit 8"`, `"auth-timeout bug"`. This is the common path.
2. **`resume <run-id>`** — pick up a halted run from its last completed stage.
3. **`status`** — list runs in `.agent-runs/` with last-stage status. Read-only.
4. **Empty** — same as `status` (a "where am I?" query).

Decide which shape by checking the first whitespace-separated token of `$ARGUMENTS`.

---

## Path 1 — start a new run (the common case)

### Step 1 — verify project is initialized

Check that `.pipelines/manifest-template.yaml` exists. If not, tell the user:

> This project hasn't been initialized for pipeline runs yet. Run `/pipeline-init` first — it reads your project (or a PRD you point at), scaffolds the `.pipelines/` directory, and prepares `CLAUDE.md`. Comes back in under a minute.

Stop. Do not improvise scaffolding.

### Step 2 — choose pipeline type

Default to `feature`. Override only if:
- `$ARGUMENTS` contains "bug" / "fix" / "regression" → `bugfix`
- `$ARGUMENTS` contains "release" / "ship" / "tag" / "module-release" → `module-release` (if `.pipelines/module-release.yaml` exists)

If you guess and you're not sure, name your guess in the next user-facing message: *"I'm reading this as a feature run. If it's a bugfix or release run, reply now; otherwise I'll proceed."*

### Step 3 — generate run id

`run_id = "{today_iso_date}-{slug}"`. `today_iso_date` is `YYYY-MM-DD` from `date +%Y-%m-%d`. `slug` is the user's description normalized: lowercase, ASCII, kebab-case, max 60 chars, drop articles/filler.

If a directory `.agent-runs/<run_id>/` already exists, append `-2`, `-3`, etc.

### Step 4 — spawn the manifest drafter

`mkdir -p .agent-runs/<run_id>/`. Initialize `run.log` with a `RUN_STARTED` line.

Then spawn a fresh subagent via the `Agent` tool, role file `.pipelines/roles/manifest-drafter.md`, with arguments:

- `run_id` — the generated id.
- `pipeline_type` — feature / bugfix / module-release.
- `user_description` — the user's verbatim `$ARGUMENTS` text.
- `project_root` — the current working directory.

The drafter walks the project root for known spec patterns, reads matched files, drafts every derivable manifest field, writes `.agent-runs/<run_id>/manifest.yaml` and `.agent-runs/<run_id>/draft-provenance.md`, and returns a one-line summary string.

### Step 5 — validate the draft

Run `python scripts/policy/check_manifest_schema.py --run <run_id>`. If it fails, re-spawn the drafter with `revision_request: "<the specific schema failure>"` and instructions to fix. Re-validate. If still fails after one revision, fall back to "partial draft" presentation at the gate.

### Step 6 — manifest gate (AskUserQuestion)

Render a brief summary of the drafted manifest in chat (top-line goal, allowed_paths, definition_of_done, advances_target). Then fire **ONE** AskUserQuestion:

- **question**: `Manifest drafted at .agent-runs/<run_id>/manifest.yaml. Approve to start the run, or block to revise.`
- **header**: `Manifest gate`
- **options**:
  - label `APPROVE` — `Start the run. Spawn the researcher next.`
  - label `Revise` — `Stop. I'll describe what to change in my next message.`
  - label `View full manifest` — `Print the complete manifest file to chat for review.`

Handle:
- `APPROVE` → log `MANIFEST_APPROVED` to run.log, proceed to Step 7
- `Revise` → wait for the user's revision text, re-spawn drafter with `revision_request:`, loop back to Step 6 (max 5 cycles)
- `View full manifest` → Read the manifest, print verbatim in chat, then immediately fire the same AskUserQuestion again

### Step 7 — orchestrate the pipeline

Read `.pipelines/<pipeline_type>.yaml`. For each stage in order:

1. **Skip if artifact exists** (resumed run): log `STAGE_SKIPPED: <name> (artifact exists)`.
2. **If `role: pipeline`**, execute the `command` field via Bash. Capture stdout+stderr to `.agent-runs/<run_id>/<artifact>`. On non-zero exit, surface the failure (see failure-message shape below) and STOP.
   - **Special case `auto-promote`**: exit 0 means ELIGIBLE (manager-decision.md was preset by auto_promote.py); exit 1 means NOT_ELIGIBLE (auto-promote-report.md names which conditions failed). Both advance the pipeline. Only exit 2 (run dir not found) is a real failure.
3. **If `role: human`** with `gate: human_approval`, this is a mid-run gate. Fire Step 8 (plan gate) or Step 9 (manager gate) per the stage name.
4. **Otherwise** (an agent role: `researcher`, `planner`, `test-writer`, `executor`, `verifier`, `drift-detector`, `critic`, `manager`), spawn a subagent via `Agent`:
   - Read `.pipelines/roles/<role>.md` in full.
   - Build the run-context block: manifest content + every prior stage's artifact file content (concatenated with `--- <filename> ---` separators).
   - Spawn description: `<role> stage for run <run-id>`.
   - Prompt: the role file content verbatim + `\n\n---\n\nRUN CONTEXT:\n` + run-context block + `\n\nRUN ID: <run-id>\nWRITE YOUR OUTPUT to .agent-runs/<run-id>/<artifact> and stop.`
   - After subagent returns, verify the artifact exists and is non-empty (`test -s` via Bash).
   - On missing/empty artifact: log `STAGE_FAILED: <name> (artifact not produced)`, surface failure, STOP.
   - On success: log `STAGE_DONE: <name>` and continue.

After each stage, append a single line to `.agent-runs/<run_id>/run.log`:
```
<ISO-timestamp> STAGE_DONE: <stage-name>
```

### Step 8 — plan gate (after `plan` stage)

After the planner writes `plan.md`, fire ONE AskUserQuestion:

- **question**: `Plan drafted. Approve to start execution, replan to revise, or block to halt.`
- **header**: `Plan gate`
- **options**:
  - label `APPROVE` — `Start execution. Spawn the executor next.`
  - label `REPLAN` — `Stop and revise. I'll describe what to change in my next message.`
  - label `View plan` — `Print plan.md to chat for review.`
  - label `Block` — `Stop the run with a finding.`

Surface (above the question) the first 3 bullets from plan.md §Summary, the files-touched count from §Blast radius (top 5), and the count of items in §Open Questions if any.

Handle as in Step 6.

### Step 9 — manager gate (after `auto-promote` stage, only if `auto_promote_aware: true` AND NO PROMOTE preset)

Before firing the gate, check if `manager-decision.md` already exists with `**Decision: PROMOTE**` as its first non-empty line. If yes, the auto-promote stage already wrote it. Spawn the manager subagent in **validate-and-append** mode (it appends a confirmation section without rewriting the verdict), log `STAGE_DONE: manager (auto-promoted)`, and skip the gate entirely.

If no preset, fire ONE AskUserQuestion:

- **question**: `Manager's recommendation: <PROMOTE | BLOCK | REPLAN>. <one-line reasoning>. Confirm?`
- **header**: `Manager gate`
- **options**:
  - label `APPROVE manager verdict` — `Accept the manager's recommendation as the final decision.`
  - label `BLOCK` — `Override manager: stop the run with a finding.`
  - label `REPLAN` — `Override manager: revise the manifest or plan.`
  - label `View manager decision` — `Print manager-decision.md to chat for review.`

Surface (above the question) the counts: verifier open items, critic findings (with structural breakdown), drift findings, and the first paragraph of manager-decision.md.

### Step 10 — final report

After the last stage:

```
Run complete: <run_id>

  Pipeline:           <type>
  Final disposition:  PROMOTED | BLOCKED | NEEDS_REPLAN
  Stages done:        <count>
  Artifacts:          .agent-runs/<run_id>/
  Auto-promoted:      <yes if manager gate skipped; no otherwise>

  Next step:          <suggested git/PR action based on disposition>
```

---

## Path 2 — resume `<run-id>`

`$ARGUMENTS` starts with `resume`. Take the second token as `run_id`.

1. Verify `.agent-runs/<run_id>/run.log` exists. If not: *"No run at `.agent-runs/<run_id>/`. Try `/run status` to see available runs."*
2. Read `run.log`. Find the last `STAGE_DONE` line. That's the resumption point.
3. Skip to Step 7 (orchestrate). The orchestrator picks up at the next stage.

If the last log line is `STAGE_FAILED` or `STAGE_BLOCKED`, surface the failure shape and fire AskUserQuestion: retry / abort / view-log.

---

## Path 3 — status (also empty `$ARGUMENTS`)

List `.agent-runs/*/` directories sorted by mtime descending. For each, read `run.log` and report a single line:

```
<run_id>      <pipeline-type>   last: <stage-name> at <relative-time>   status: <RUNNING | HALTED_AT_GATE | DONE | FAILED>
```

Maximum 10 rows. If more exist, suffix `(... <N> older)`.

---

## Hard rules

- **One slash command per project session.** If a `/run` is already in flight (the most recent `.agent-runs/<run_id>/run.log` ends in `STAGE_STARTED` without a paired `STAGE_DONE`), refuse to start a new one; offer `resume` or explicit abort.
- **Use AskUserQuestion for ALL three gates.** No chat-message-with-special-syntax gates. The v1.2.x failure mode was the LLM inventing extra prompts or chickening out at the gate; modal AskUserQuestion eliminates the interpretive surface.
- **(v2.1.0) AskUserQuestion is permitted ONLY at the three declared gates** (manifest, plan, manager). The modal-budget hook (`hooks/hook_utils.py:modal_budget_decision`) enforces this — any AskUserQuestion fired when `current_stage` is not a declared `gate: human_approval` stage returns deny. The v1.3.0 design eliminated chat-APPROVE ceremony; the v2.0.x failure mode was the orchestrator manufacturing extra modals between gates (turning 3 clicks per run into 15+). v2.1.0 closes that loophole. See "Adopt-and-proceed" below for the corrected pattern.
- **Never re-fire a gate after it advanced.** Once APPROVE returns, the next message advances to the next stage. Do not re-ask for confirmation.
- **Never proceed past a failed validation by guessing.** Surface the failure with remediation pointers; let the user steer.
- **Never write outside `.agent-runs/<run_id>/` and the project working tree** that the pipeline stages themselves modify.
- **Auto-promote is evidence-driven, not authorization-driven.** If `auto_promote.py` says ELIGIBLE, the gate is skipped automatically. If it says NOT_ELIGIBLE, the human gate fires — no override.

## Adopt-and-proceed (v2.1.0)

When a stage (researcher, planner, executor, verifier, critic, etc.) returns recommendations on decisions, the default behavior is:

1. **ADOPT** the recommendations the role surfaced. Roles like researcher are explicitly designed to "give a recommendation but defer the final choice to the human director." That deferral does NOT mean "the orchestrator must fire a modal for each one." It means the recommendation lives in the artifact and the director can review it post-hoc.
2. **RECORD** the choices in `.agent-runs/<run-id>/director-decisions.md` as bound. Include the reasoning and cite the upstream artifact (e.g. "DR-A: civiccast 14-branch count — research §1; recommendation adopted").
3. **NARRATE** in chat one line per decision so the operator sees what was adopted.
4. **PROCEED** to the next stage without firing a modal.

Modal AskUserQuestion fires ONLY when:
- (a) the decision is outside the operator's arc authorization (e.g. forbidden-zone repo, irreversible op the operator hasn't pre-approved), OR
- (b) the choice is genuinely two equally-strong options with no analytical basis to prefer one (rare; if the researcher had data to recommend, this isn't the case), OR
- (c) the modal IS the framework's declared gate (manifest, plan, manager).

Memory rules about "ask before deciding" (e.g. `feedback_no_unilateral_product_decisions.md` in the operator's ad-hoc memory layer) are SUSPENDED during active pipeline runs. The pipeline's gate budget is the authoritative ask-or-decide policy; broader memory rules that conflict with it apply outside pipeline runs only.

The modal-budget hook enforces this mechanically: if you try to fire AskUserQuestion at a non-gate stage, the hook returns deny with a structured reason naming the legitimate gates and pointing at this section.

## Failure-message shape (all error surfaces)

Every failure the user sees follows this shape:

```
<one-line summary of what failed>

  What happened: <one sentence in plain language>
  Where:         <file path or stage name>
  Suggestion:    <concrete next action>

  Full context:  <path to artifact with details, if any>
```

No raw Python tracebacks in chat. No "check_xxx: FAIL" output. The orchestrator translates every error into the shape above before showing the user.
