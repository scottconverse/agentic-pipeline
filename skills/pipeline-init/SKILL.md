---
name: pipeline-init
description: Initialize a project for pipeline runs. Inspects what the project already has (spec, release plan, CLAUDE.md, tests, CI), produces an orientation summary, gates scaffolding behind a modal AskUserQuestion, then scaffolds .pipelines/, scripts/policy/, and a starter CLAUDE.md if missing. Invoked as /agent-pipeline-claude:pipeline-init.
---

# Pipeline-init

Follow the canonical workflow in `references/pipeline-init.md`. That document is the single source of truth for orientation, scaffolding contents, the gate flow, greenfield handling, and re-init handling.

Tool mapping for Claude Code:

- Use **Bash** for `git status`, `ls`, `git log` orientation.
- Use **Read** to inspect the project's existing spec / release plan / CLAUDE.md.
- Use **Write** for scaffolded files; use **Edit** for amending an existing CLAUDE.md only after the operator approves via the modal gate.
- **Render the orientation summary as a plain chat message** so the operator can read what was detected, **then immediately invoke `AskUserQuestion`** for the approve / wait / cancel decision. v1.3.0 retired the free-form "Reply APPROVE" chat-text gate across the plugin (see CHANGELOG v1.3.0); pipeline-init aligns with that for v2.0 (audit Cluster E). The chat summary is informational; the modal is the decision.

`$ARGUMENTS` is one of: empty (inspect cwd), a file path (read as PRD), a URL (`git clone` first), or a description paragraph (greenfield mode).

Hard rules:

- Never overwrite an existing `CLAUDE.md` without an explicit modal approval via `AskUserQuestion`.
- Never overwrite an existing `.pipelines/` directory; treat as re-init and gate the subset-to-refresh question through `AskUserQuestion`.
- Never copy any file outside the project root.
- Never read or modify the plugin's own marketplace dir under `~/.claude/plugins/marketplaces/`.
- Always produce the orientation summary BEFORE the modal gate fires.
