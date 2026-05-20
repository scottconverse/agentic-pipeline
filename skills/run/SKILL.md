---
name: run
description: Start, resume, or list pipeline runs. Drafts a per-run scope contract from the project's spec, presents it in chat with a deterministic keyword gate (reply APPROVE / REVISE / VIEW), then orchestrates research → plan → execute → verify → critique end-to-end. Auto-promotes evidence-driven when all checks pass; otherwise prints a chat gate prompt at each human gate (APPROVE / REPLAN / BLOCK / VIEW; first non-whitespace token, case-insensitive). Invoked as /agent-pipeline-claude:run.
---

# Run

Follow the canonical workflow in `references/run.md`. That document is the single source of truth for argument shapes, the manifest gate, the plan gate, the manager gate, stage orchestration, resume logic, and status listing.

Tool mapping for Claude Code:

- When the procedure says **`Agent`**, use the Agent tool to spawn a subagent with the appropriate role file from `.pipelines/roles/`.
- When the procedure says **`Bash`**, use the Bash tool from the project root.
- When the procedure says **chat gate**, print the structured gate prompt at the end of your reply (`APPROVE` / `REVISE` / `REPLAN` / `BLOCK` / `VIEW` as appropriate per Step 6/8/9), then stop and wait for the operator's next message. Parse the first non-whitespace token, case-insensitive. Do NOT invoke `AskUserQuestion` for gates — the modal-budget hook denies it with `MODAL_BUDGET_EXCEEDED` during an active non-drafting run.

`$ARGUMENTS` is the user's text after the slash command. The procedure parses its first whitespace-separated token to decide between new run / `resume` / `status`.

Hard rules:

- **(v1.2.0+) Read the project's control plane BEFORE drafting the manifest.** Look at `.agent-workflows/PROJECT_CONTROL_PLANE.md`, `.agent-workflows/ACTIVE_WORK_QUEUE.md`, `docs/RELEASE_PLAN.md`, or `docs/PROJECT_CONTROL_PLANE.md` (first one that exists wins). The control plane names the active target. If the user's task description does not align with that target, STOP and either propose alignment OR ask the user to set `override_active_target` with a 2+ sentence reason. Do not draft an off-priority manifest. `check_active_target.py` enforces this at preflight stage 0.5; surfacing the conflict at draft time is faster + cheaper.
- **(v1.2.0+) Manifest requires `advances_target` and `authorizing_source`.** The drafter populates them from the control plane. Without a control plane, set `advances_target` from the user description and `authorizing_source` empty (preflight runs informational mode).
- **(v2.2.1) Gates fire as chat prompts with deterministic first-token keyword parsing.** No modal `AskUserQuestion`. The prompt names the recognized keywords (`APPROVE` / `REVISE` / `REPLAN` / `BLOCK` / `VIEW`, case-insensitive); the orchestrator parses the operator's first non-whitespace token. Anything unrecognized re-prints the gate with a no-parse note. v1.3.0 → v2.1.0 routed gates through modal `AskUserQuestion`, but Cowork's modal overlay hid the chat context the operator needed at gate-decision time, so v2.2.1 reverses to chat. The interpretive-surface concern that drove the modal redesign is structurally addressed by the modal-budget hook (`MODAL_BUDGET_EXCEEDED` on every `AskUserQuestion` during active non-drafting runs), the explicit keyword grammar, and the no-parse re-print branch.
- **(v1.3.0) Auto-promote is the path to hands-off.** When `auto_promote.py` reports ELIGIBLE (verifier clean, critic clean, drift clean, policy passed, tests passed), the manager stage skips the human gate entirely. The manager subagent runs in validate-and-append mode. This is the ONLY automation mechanism — no separate "autonomous mode," no grant files, no signed authorization documents.
- **(v1.3.0) Operations the run skill never performs autonomously:**
  - Admin-merging a PR (`gh pr merge --admin`)
  - Pushing a tag (`git push origin <tag>` or `git push --tags`)
  - Creating a release (`gh release create`)
  - Force-pushing (`git push --force`)
  - Modifying shared state outside `manifest.allowed_paths`
  These are operator-driven outside the pipeline. The pipeline opens PRs; the operator merges them.
- Never silently skip a stage.
- Never advance past a `BLOCKED` or `FAILED` stage.
- Never rewrite `run.log` (append-only).
- Never modify the manifest mid-run. `check_manifest_immutable.py --check` will catch mutations and fail the policy stage.
- Never write outside `.agent-runs/<run_id>/` and the project working tree.
- At any halt, give the exact resume instruction: `/agent-pipeline-claude:run resume <run-id>`.
