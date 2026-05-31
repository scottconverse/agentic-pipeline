# agent-pipeline-claude v0.4 — the judge layer

> **Historical discussion seed (v0.4).** This announced the judge layer when it was new. Some references are now stale: `commands/` was removed (the original "Handler 3a" interceptor proved impossible on this platform and was re-realized differently — see `CHANGELOG.md` v2.3.0), and the manual section is now titled "The judge layer" (no "(v0.4)"). For the current design see `ARCHITECTURE.md` and `USER-MANUAL.md`.

**TL;DR:** Real-time action-level supervision inside the executor stage. Opt-in via `.pipelines/action-classification.yaml`. The orchestrator classifies every executor tool call by risk class and spawns a context-isolated judge subagent for the dangerous ones. Four verdicts: `allow`, `block`, `revise`, `escalate`.

## What this catches

The other gates run **before** or **after** the executor:

- Manifest gate catches wrong scope (before any stage runs).
- Plan gate catches wrong approach (before code is written).
- Policy stage catches path violations, TODOs, ADR changes (after executor).
- Verifier and manager catch incomplete work (after policy).

None of them catch a destructive or external action **while** the executor is doing it. The Lindy case (May 2026) was the receipt: an agent sent 14 unauthorized emails because operator-trained-reflex APPROVE clicking had defeated manual confirmation prompts. Prompts don't hold across long context windows.

The judge layer adds a fifth gate at the action boundary: every tool call the executor proposes is classified by risk class and routed.

- `read_only` (e.g. `cat`, `grep`, `git log`) → execute + log.
- `reversible_write` (e.g. `str_replace_editor`, `git commit`, `cp`) → execute + log.
- `external_facing` (e.g. `git push` to non-main, `gh pr create`, `curl -X POST`) → STOP, spawn judge.
- `high_risk` (e.g. `rm -rf`, `git push --force`, `git push main`, `DROP TABLE`, `npm publish`) → STOP, spawn judge, **and** require human confirm even after the judge ALLOWs.

The judge subagent receives the manifest, the matched action policy, prior judge decisions for the run, and the structured action proposal — but **not** the executor's reasoning chain. That isolation is the mechanism: the judge cannot be persuaded by the same chain of inferences that produced the over-broad action in the first place.

## The four verdicts

- **`allow`** — within scope, evidence supports the justification. Proceed.
- **`block`** — outside scope, violates policy, or unsupported by evidence. Halt the pipeline.
- **`revise`** — legitimate basis, wrong form. Send a concrete revision instruction back to the executor (e.g. "push to the feature branch in the manifest, not to main"). 3-cycle cap per action_id, auto-escalate after.
- **`escalate`** — requires human judgment (money, credentials, legal, judge confidence < 0.7). Pipeline pauses for a specific human question.

## Two new artifacts per run

When the judge layer is active:

- `judge-log.yaml` — chronological log of every action: tool, arguments, class, disposition (`auto_allow` / `judged_allow` / `judged_revise` / `judged_block` / `judged_escalate` / `human_confirmed` / `human_blocked`), judge reason, revision instruction.
- `judge-metrics.yaml` — counts by class and disposition, `escalation_rate`, `judge_invocations`, `revision_cycles`.

The escalation rate is the operator's tuning signal. Too low → rules too permissive. Too high → cookie-banner effect arriving in slow motion. Healthy range (rough guide): 0.02 – 0.10.

## Enabling it

Two ways:

1. **Via `/pipeline-init`** — when scaffolding a project, accept the offer to copy `.pipelines/action-classification.yaml` along with the rest of the pipeline files.
2. **Manually** — `cp <plugin-path>/pipelines/action-classification.yaml .pipelines/` in any project that already uses the plugin.

Disable by deleting the file. The next run reverts to v0.3 executor behavior.

## Files to look at first

- `pipelines/roles/judge.md` — the role file the judge subagent receives.
- `pipelines/action-classification.yaml` — the rules. Edit for your project's specific dangerous commands.
- `commands/run-pipeline.md` §"Handler 3a" — the classify → judge → execute inner loop.
- `ARCHITECTURE.md` §7 — diagrams and design rationale, including the context-isolation model.
- `USER-MANUAL.md` §"The judge layer (v0.4)" — operator-facing guidance, especially the escalation-rate tuning section.
- `CHANGELOG.md` §"[0.4.0]" — full Added / Why / Stacking / Known-limitations breakdown.

## Honest limitations

These ship in the CHANGELOG, repeating them here so they reach a wider audience:

- **Single-model-family blind spots may still correlate.** Context isolation reduces this risk; it does not eliminate it. Two agents in the same family can share blind spots that no amount of context separation will catch.
- **The judge is slower than no-judge.** Every `external_facing` and `high_risk` action incurs a subagent spawn. Negligible for read-heavy stages; real wallclock cost for stages with many external operations.
- **Rules drift.** The shipped classification covers common patterns. Your project's specific dangerous commands need to be added — until they are, those actions fall into the default class (`reversible_write`).
- **The judge cannot see future state.** It evaluates one action at a time. A sequence of individually-authorized actions that compose into an unauthorized outcome is caught by the policy stage and the verifier, not the judge.

## Stacking

- v0.2 — `module-release` pipeline. Catches execution-cascade failures. **Pre-executor.**
- v0.3 — Dual-AI audit-handoff. Catches drift failures. **Post-executor.**
- **v0.4 — Judge layer. Catches unauthorized actions in real time. *During* executor.**

The three address three different failure classes and can be enabled independently. Many projects will run all three.

## Where to ask

- Bug or unexpected behavior → Issues
- "How do I…" question → Q&A category on this Discussions board
- "Here's what I built with it" → Show and tell
- "v0.5 should…" → Ideas

The CHANGELOG entry and ARCHITECTURE.md §7 are the source of truth. This post is the announcement.
