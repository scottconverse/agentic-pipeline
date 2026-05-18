---
name: intake
description: Capture a plain-English product, repo, design, task, bug, or feature description and draft `.agent-runs/<run-id>/intake.md`, `manifest.yaml`, and `scope-lock.yaml` without starting the pipeline.
---

# Intake

Follow the canonical workflow in `references/intake.md`, adapted for Claude Code:

- Treat `$ARGUMENTS` as the user's message content or explicit arguments.
- Use the Bash tool for date and directory checks.
- Use the Write tool for new files inside `.agent-runs/<run-id>/` only.
- Ask exactly one direct question when the user has not provided a usable
  description, then stop.

Stop after draft intake artifacts are created and summarized. Do not validate
the manifest, run policy checks, spawn agents, start `run`, or modify
`pipelines/` templates.
