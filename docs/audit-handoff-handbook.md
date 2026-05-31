# Audit-Handoff Handbook — agent-pipeline-claude v0.3

> **Current as of v3.0.1.** The `v0.3` tag marks where the audit-handoff discipline originated, not stale content — it still applies as described. See `CHANGELOG.md` for anything that changed since.

This is the operator's reference for the dual-AI audit-handoff discipline. It exists because long autonomous runs by a single AI accumulate drift faster than they accumulate features, and a second AI reading the durable artifacts cold catches what the first AI missed.

## What this handbook covers

The `/audit-init` command and the artifacts it produces:
- `<PROJECT>_AUDIT_GATE.md` — short mandatory gate read every audit turn (out-of-repo)
- `<PROJECT>_AUDIT_PROTOCOL.md` — long reference protocol (out-of-repo)
- `<project>/docs/process/5-lens-self-audit.md` — in-repo shared discipline
- Per-agent wiring (Claude memory feedback file on the Claude side; the second AI's project-context or skill-registration file on the other side; or manual instructions for runtimes without a standing-instructions surface)

## When to use the audit-handoff discipline

Use it when:
- The project has two AI systems with separate context (e.g., Claude in Claude Code paired with a second AI in its own runtime).
- Long autonomous runs are accumulating drift that humans catch only on close review.
- "Implementing agent says done, auditor says no" is a recurring pattern.
- Status-word abuse (`ready`, `green`, `taggable`, `shippable`) is sneaking into chat reports.
- Durable docs (CHANGELOG, HANDOFF, ledger) drift in parallel with code commits.

Don't use it for:
- One-off projects with no release artifacts.
- Solo human work where the human is the verifier.
- Projects where both roles run as the same agent (the structural benefit comes from independent context).

## The two halves of the discipline

### Implementing-side (5-lens self-audit before push)

The implementing agent runs a hostile audit on its own diff before push. Five lenses:
1. Engineering — every name/path/version is grep-verified.
2. UX — every user-visible string is read cold.
3. Tests — every claim that code "passes" is verified to actually ASSERT, not just exercise.
4. Docs — every code change is reflected in README/CHANGELOG/HANDOFF/PR-body/ledger.
5. QA — read the final state, not the diff. Cross-file contradictions surface here.

Plus an artifact-state checklist of project-specific drift items that the auditor has surfaced over time.

Plus a post-push SHA-propagation step (update PR body, HANDOFF, ledger run IDs to the new HEAD).

### Verifying-side (10-section audit output)

The auditing agent reads the implementer's report cold and produces a 10-section output:
1. Verdict.
2. Claim Verification Matrix (every claim, evidence, source, verdict).
3. Durable Artifact Reads (which docs, with content cited).
4. Substantive Content Checks (test bodies, not test names; code content, not just file existence).
5. Drift Matrix (chat vs. local vs. durable vs. live remote).
6. Working Tree And Live Remote State.
7. Unreported Catches (what the implementer didn't surface).
8. Open Caveats / Release Risks.
9. Paste-Ready Directive (exact file/line/bad-text/replacement for the implementer's next turn).
10. Recommended Next Action.

The auditor never produces a "Standing by" or "Approve" answer that lacks section 9. Even when cleanup is complete, section 9 contains the next-phase directive.

## How they interact

The implementing agent reads the in-repo `docs/process/5-lens-self-audit.md`. The auditor reads the out-of-repo gate + protocol. Both reference each other in their cross-references blocks.

When the auditor finds a drift pattern the implementer should have caught, the auditor's directive does two things:
1. References the relevant lens or artifact-state checklist item by name.
2. Adds a new entry to section 22 of the protocol AND, if generic enough, a new artifact-state checklist item to the in-repo 5-lens doc.

This is how the discipline gets stronger over time. Each audit cycle that finds drift updates the durable rule, so the next cycle catches the same pattern at push time instead of audit time.

## Role-agent matrix

The discipline is symmetric — any agent can play either role:

| Project | Implementer | Auditor |
|---|---|---|
| Example A | Claude | (a second AI) |
| Example B | (a second AI) | Claude |
| Your project | (your choice) | (your choice) |

The plugin itself runs in Claude Code. The second AI can be any tool whose project-context or standing-instructions surface lets you point it at the gate and protocol files. The discipline is the artifact set; the runtimes are interchangeable.

`/audit-init` asks for the role assignment and wires the per-agent pointers accordingly.

## Stacking with the pipeline

The `module-release` pipeline (v0.2) and the audit-handoff discipline (v0.3) are complementary:

- **Pipeline** catches execution-cascade failures: pre-existing CI infrastructure bugs surfacing one at a time during release, tag-move dances, halt-and-ask loops on routine classifications.
- **Audit-handoff** catches drift failures: wrong endpoint in PR body, stale CHANGELOG, "Closed" without evidence, status-word abuse, durable docs drifting in parallel.

They address different failure modes. Use both for projects with two AI systems.

The pipeline's Phase 1 (Scoped product work) is where the implementer's 5-lens self-audit fires. The pipeline's Phase 4 (Verifier) is where the auditor's 10-section output fires. The pipeline's human gates remain unchanged.

## Honest expectations

What the discipline reduces:
- Drift items per audit cycle: ~5-15 per cycle on first pass, dropping as artifact-state checklist absorbs patterns.
- "Chat-promise" follow-through gaps: nearly eliminated once the implementer's report format includes the 5-lens block.
- Status-word abuse: ~zero after a few cycles of the auditor rejecting non-allowed words.
- Stale PR bodies and HANDOFFs after pushes: nearly eliminated by the post-push SHA-propagation step.

What it does NOT prevent:
- Wrong-direction product decisions. The audit verifies execution, not strategy.
- Cascading CI infrastructure bugs. That's what the pipeline's Phase 0 is for.
- Single-agent runs (where there's no independent verifier). Discipline collapses to self-audit-only.
- Agent judgment errors on novel scenarios. Halt-and-ask gates exist for this.

## Why the in-repo doc lands via PR, not push to main

The in-repo `docs/process/5-lens-self-audit.md` is process documentation that both agents read. It should ride through the project's normal review pipeline so:
1. The human director reviews the wording before it becomes binding.
2. Future changes to the doc (new artifact-state items) follow the same path.
3. The doc shows up in `git log --diff-filter=A` for auditors looking up when the discipline started.

`/audit-init` opens a PR with the doc and lets the user merge. Skipping the PR step is allowed but loses the audit trail benefit.

## Why the out-of-repo gate and protocol are not in the repo

They live at the desktop level (outside any project repo) because:
1. They govern the auditor's behavior when verifying. The auditor often reads them BEFORE entering the repo (to know what to look for).
2. They reference paths and conventions specific to the local environment.
3. They can be updated without dragging a PR through the project's review pipeline (when the human director wants to tighten verification standards mid-sprint).

The cost: they're not version-controlled in the project's history. The mitigation: section 22 of the protocol IS the version-controlled history of drift patterns, and the in-repo 5-lens doc tracks the artifact-state checklist evolution.

## Initial setup vs. ongoing operation

**Initial setup** (per project): one-time. Run `/audit-init`, answer the prompts, merge the PR, verify each agent reads its pointer on next session.

**Ongoing operation**: zero overhead. The implementer reads its memory/skill pointer on session start, runs the 5-lens before push. The auditor reads its memory/skill pointer + the gate on session start, produces the 10-section output. The shared in-repo doc accumulates new artifact-state items as audits find them.

The discipline is self-maintaining. The drift catalog in section 22 of the protocol IS the maintenance artifact — every new pattern that gets logged makes the next cycle a little tighter.

## See also

- `commands/audit-init.md` — command logic
- `pipelines/roles/cross-agent-auditor.md` — verifier role file
- `pipelines/roles/implementer-pre-push.md` — implementer role file
- `pipelines/templates/audit-gate-template.md` — gate template
- `pipelines/templates/audit-protocol-template.md` — protocol template
- `pipelines/templates/5-lens-self-audit-template.md` — in-repo doc template
- `docs/module-release-handbook.md` — pairs with this discipline for module releases
