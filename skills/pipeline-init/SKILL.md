---
name: pipeline-init
description: Initialize a project for pipeline runs. Inspects what the project already has (spec, release plan, CLAUDE.md, tests, CI), produces an orientation summary, then prints a chat gate (reply APPROVE / WAIT / CANCEL, case-insensitive) before scaffolding .pipelines/, scripts/policy/, and a starter CLAUDE.md if missing. Invoked as /agent-pipeline-claude:pipeline-init.
---

# Pipeline-init

Follow the canonical workflow in `references/pipeline-init.md`. That document is the single source of truth for orientation, scaffolding contents, the gate flow, greenfield handling, and re-init handling.

Tool mapping for Claude Code:

- Use **Bash** for `git status`, `ls`, `git log` orientation.
- Use **Read** to inspect the project's existing spec / release plan / CLAUDE.md.
- Use **Write** for scaffolded files; use **Edit** for amending an existing CLAUDE.md only after the operator types `APPROVE` in chat at the gate prompt.
- **Render the orientation summary as a chat message followed by the chat gate prompt** (recognized keywords: `APPROVE` / `WAIT` / `CANCEL`, case-insensitive). Stop and wait for the operator's next message; parse the first non-whitespace token. Do NOT invoke `AskUserQuestion` — pipeline-init runs before any active pipeline run exists, but for consistency with the v2.2.1 chat-gate design the orientation gate uses the same chat keyword pattern. v1.3.0 → v2.1.0 routed this gate through `AskUserQuestion` modals; v2.2.1 reverses to chat (see CHANGELOG v2.2.1) after the operator-UX failure (Cowork's modal overlay hid chat context at gate-decision time).

`$ARGUMENTS` is one of: empty (inspect cwd), a file path (read as PRD), a URL (`git clone` first), or a description paragraph (greenfield mode).

Hard rules:

- Never overwrite an existing `CLAUDE.md` without an explicit `APPROVE` reply from the operator at the chat gate prompt.
- Never overwrite an existing `.pipelines/` directory; treat as re-init and print the subset-to-refresh chat gate (`Role files` / `Policy scripts` / `Everything` / `Cancel` as recognized keywords).
- Never copy any file outside the project root.
- Never read or modify the plugin's own marketplace dir under `~/.claude/plugins/marketplaces/`.
- Always produce the orientation summary BEFORE the chat gate prompt.
