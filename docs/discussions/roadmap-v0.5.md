# Roadmap — v0.5 candidate set

> **Historical discussion seed (v0.5).** Several candidates here have since shipped or changed: the "Project memory layer" landed as file-backed + Mem0 memory in v2.0, and autonomous-mode vestiges were removed in v3.0.0 (WS-8). Read this as a point-in-time snapshot, not the current roadmap — see `CHANGELOG.md`.

This is a living document. The v0.5 candidate set crystallizes as ideas from the [Ideas](ideas-seed.md) thread accumulate evidence — projects that needed it, receipts of where the gap bit, sketches that converge on a concrete design.

The candidate set is **not a commitment.** v0.5 will ship some subset of this; the rest will roll to v0.6 or get dropped if they don't accumulate enough receipts.

## What's plausible for v0.5

Roughly in order of how much evidence each has accumulated.

### Candidate 1 — Project memory layer

**Status:** Sketch + early consensus.

A structured per-project memory file the manifest can reference. Lives at `.pipelines/project-memory.yaml`. Captures things that CLAUDE.md doesn't have a natural home for: which paths are "stable surface" vs. "rough edge," recurring drift patterns the auditor has surfaced, project-specific failure receipts that should warn future executor runs.

**Why now:** v0.3's audit-handoff discipline produces a stream of drift findings; they currently land in ad-hoc CLAUDE.md edits or scattered ADRs. A structured home would let the v0.4 judge, the policy stage, and the verifier all reference the same record.

**Open design questions:**

- Schema. YAML keyed by path / module / category?
- Update flow. Who writes — the auditor on every audit, the manager on every PROMOTE, both?
- Read flow. Which roles consume it? Researcher and planner for sure; what about the judge?

### Candidate 2 — Per-stage budgets and timeouts

**Status:** Sketch.

A `timeout_minutes` and optional `max_tokens` field on each stage entry in the pipeline YAML. Orchestrator enforces via subagent kill when exceeded. Logs a clear `BUDGET_EXCEEDED` outcome so resume picks up correctly.

**Why now:** Multiple receipts of agent stages spinning indefinitely — researcher rabbit-holing on adjacent code, executor retry-looping on a flaky test, verifier scanning more than the run's diff range.

**Open design questions:**

- Default budget per stage? Or only enforced when the field is present?
- Wall-clock or token-spend or both?
- What's the right killer-of-last-resort if subagent kill doesn't respond?

### Candidate 3 — Better PRD-to-manifest auto-fill

**Status:** Sketch.

A `/manifest-from-prd` slash command that reads the PRD, the recently-touched code, and proposes a draft manifest for the operator to edit. Reduces manifest authoring from ~10 minutes to ~2 minutes for repeat projects.

**Why now:** The manifest authoring time is the single biggest friction point in adopting the plugin on a new project. Operators who skip fields out of fatigue produce worse runs.

**Open design questions:**

- Where does the auto-fill stop? `goal` and `allowed_paths` are reasonable; `definition_of_done` is the operator's call.
- Do we surface an "is this what you meant?" diff after fill?
- What's the failure mode if the PRD is missing or ambiguous?

### Candidate 4 — Auto-tuning the v0.4 classification rules

**Status:** Concept.

A `/judge-tune` slash command that reads `judge-metrics.yaml` across runs in the project, identifies patterns (always-allowed actions worth promoting to read_only, always-blocked actions worth promoting to high_risk), and proposes specific edits to `action-classification.yaml`.

**Why now:** v0.4 just shipped. As projects accumulate run history, the operator-tuning step is currently manual ("look at the log, eyeball patterns"). An assistant for that step is the obvious next layer.

**Open design questions:**

- How many runs of evidence before a tuning proposal? Five? Twenty?
- Does the tuner write directly or propose a PR-style diff?
- Does the tuner know about project-specific rules already added, so it doesn't propose duplicates?

## What's plausible for v0.6+

Listed for transparency, not committed.

- **Multi-repo orchestration** (`meta-release` pipeline type for projects like CivicSuite). Significant scope; needs at least one concrete cross-repo failure receipt before design.
- **Streaming verifier output** for long-running verifications.
- **Cross-run artifact diff** for stuck runs.
- **Judge layer for non-executor stages** (researcher, planner) — likely overkill; only ship if a specific class of misuse emerges.

## What's been considered and rejected

For the record:

- **Autonomous mode.** Explicitly forbidden by the plugin's hard rules. Adding it would defeat the design.
- **Optional gates.** Same reason. The three human gates are the minimum-viable structure.
- **Replacing the policy stage with hooks.** The two operate at different grains and both are needed.
- **Auto-generating the manifest** (vs. auto-filling drafts). Auto-generation defeats the gate; auto-fill of drafts is fine.

## How this roadmap moves

The roadmap is a living artifact. When a candidate accumulates enough receipts (typically: three projects independently hitting the same gap), it moves to the v0.5 commitment list. When v0.5 ships, this file gets renamed to `roadmap-v0.5-shipped.md` and a new `roadmap-v0.6.md` takes its place.

To push a candidate up, comment on this thread with a specific receipt: "On project X, task Y, the failure mode was Z, and the existing layers didn't catch it because…" Receipts move candidates; opinions don't.

## How to propose a new candidate

See [ideas-seed.md](ideas-seed.md). Ideas that accumulate evidence become candidates. Candidates that accumulate evidence ship.
