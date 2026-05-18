---
name: show-run-status
description: Summarize an Agent Pipeline run from .agent-runs/<run-id>/ without resuming or mutating it.
---

# show-run-status

Use this skill when the user asks where a pipeline run is, what is blocking it,
what the next required action is, or wants status without resuming the run.

## Inputs

- A run id under `.agent-runs/`.

## Workflow

1. Identify the project root and run id.
2. Run:

   ```bash
   python scripts/show_run_status.py --run <run-id>
   ```

3. Report the summary in plain language.
4. Do not edit files, resume stages, or make a pipeline decision from this skill alone.

## Stop Rule

This skill is read-only. If the user asks to continue after reading status, switch
to `run` (`/agent-pipeline-claude:run resume <run-id>`) for the next turn or
proceed directly only when they have already authorized continuation.
