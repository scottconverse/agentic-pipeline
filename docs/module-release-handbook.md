# Module Release Handbook — agent-pipeline-claude v0.2

This is the operator's reference for running a single-module release sprint end-to-end in one continuous run. It exists because the CivicSuite recovery sweep of 2026-05-10 took ~8 hours on a single module migration when the work itself was ~1 hour of product code — the rest was cascading discovery of pre-existing infrastructure bugs.

## What this handbook covers

The `module-release` pipeline:
- Phase 0 — Infrastructure preflight (NEW)
- Phase 1 — Scoped product work
- Phase 2 — Local release rehearsal (NEW)
- Phase 3 — Remote release + umbrella reconciliation
- Phase 4 — Verifier
- Phase 5 — Manager (completion handoff)

## Why each phase exists

### Phase 0 prevents the cascade

The civicrecords-ai v1.5.0 sprint surfaced THREE latent bugs in `release.yml`:
1. YAML parse error in release-notes HEREDOC (audit TEST-022 — known broken for weeks)
2. Windows runner attempting Linux Docker Compose
3. Hermetic `.env` using reserved-domain email

Each surfaced sequentially during Phase 3 (remote release). Each required a PR + merge + tag-move + 4-min CI cycle. Total cascade cost: ~6 hours.

Phase 0 inverts this. Before product work starts, audit the release infrastructure. Find bugs by reading the workflow, running it locally on fresh state, and cross-referencing the audit punchlist. Fix all of them in ONE bundled PR. Then product work begins.

**Cost of Phase 0**: 15-30 minutes per sprint when run.
**Cost of skipping Phase 0**: every latent bug becomes a Phase 3 cycle. CivicSuite v1.5.0's actual receipts: 4 PRs × ~90 minutes per cycle.

### Phase 1's self-classification rules prevent halt-and-ask cycles

The civicrecords-ai sweep's chat had ~25% of its content as "agent halted, asked permission, human answered, agent continued." Most of those halts were on routine classifications (URL update, version-string update, frozen-doc skip, shape-guard skip) that should have been mechanical.

Phase 1 ships with explicit rules for these classes pre-authorized. Halt-and-ask is reserved for genuine novelty (production source change, contract change requiring auditor approval, true ambiguity).

See `pipelines/self-classification-rules.md`.

### Phase 2 prevents the tag-move dance

The civicrecords-ai v1.5.0 tag moved FOUR times during recovery (a0b1c467 → 31ffd87 → fc93ab03 → 917e4d5a → f18922dc). Each move was authorized — each fixed a real bug — but each cost ~30 minutes of plumbing.

Phase 2 forces the agent to run the release sequence locally before pushing the tag. The release workflow becomes the EXECUTION mechanism, not the DISCOVERY mechanism. Target: zero tag moves per sprint. Acceptable: one move for genuine environmental surprise.

See `pipelines/roles/local-rehearsal.md`.

## Running the pipeline

### Initial setup (once per project)

1. Install agent-pipeline-claude plugin:
   ```bash
   # As a Claude Code plugin
   /plugin marketplace add scottconverse/agent-pipeline-claude
   /plugin install agent-pipeline-claude@agent-pipeline-claude
   ```

   The plugin runs in Claude Code. Projects using a second AI for audit (v0.3 audit-handoff) wire that AI separately via its own standing-instructions surface.

2. Run `/pipeline-init` to scaffold:
   ```
   .pipelines/
   ├── module-release.yaml
   ├── manifest-module-release-template.yaml
   ├── roles/
   │   ├── preflight-auditor.md
   │   ├── executor.md (existing)
   │   ├── local-rehearsal.md
   │   ├── verifier.md (existing)
   │   └── manager.md (existing)
   scripts/
   ├── preflight_infrastructure.py
   └── policy/ (existing)
   ```

### Running a module release sprint

#### v1.0 way (recommended)

```bash
# Single command — drafter reads your module's spec + release plan + scope docs
/run "release <module-name> v<version> with civiccore v<version> migration"

# Example: civicrecords-ai migration
/run "release civicrecords-ai v1.5.0 with civiccore v1.0.1 migration"
```

The manifest-drafter detects the "release" keyword in your description and selects the `module-release` pipeline automatically. It then walks the module's existing artifacts (module README, CivicSuite spec, release-lockstep config, downstream pins) and drafts the full manifest including the release-specific fields (`module.*`, `release.type`, `release.expected_artifacts`, etc.) for your review.

You reply `APPROVE` at the chat gate prompt (v2.2.1 chat-keyword grammar; recognized words are `APPROVE` / `REVISE` / `VIEW`, case-insensitive — the orchestrator parses the first non-whitespace token of your reply). The orchestrator runs the four-phase pipeline.

#### v0.5.x way (deprecated; works in v1.0 via shim, removed at v1.1)

```bash
# Initialize a new run with the module-release pipeline
/new-run module-release <module-slug>

# Example: civicrecords-ai migration
/new-run module-release civicrecords-ai-civiccore-v1.0.1-migration
```

This creates `.agent-runs/<run-id>/manifest.yaml`. Fill in:

```yaml
pipeline_run:
  id: civicrecords-ai-civiccore-v1.0.1-migration
  pipeline: module-release

  module:
    name: civicrecords-ai
    repo: CivicSuite/civicrecords-ai
    local_path: /path/to/civicrecords-ai      # absolute path on operator's machine
    target_version: "1.5.0"
    previous_version: "1.4.10"

  goal: >
    Migrate civicrecords-ai from civiccore v0.22.1 to v1.0.1.
    Cut civicrecords-ai v1.5.0.
    Reconcile umbrella truth via release-lockstep-gate.

  allowed_paths:
    - civicrecords-ai/**
    - CivicSuite/docs/CivicSuiteUnifiedSpec.md
    - CivicSuite/scripts/verify-suite-state.py
    - CivicSuite/installer/modules.json
    - CivicSuite/CHANGELOG.md
    - CivicSuite/docs/release-recovery-status.md
    - CivicSuite/docs/release-lockstep/downstream-pins.md
    - CivicSuite/.agent-workflows/**

  forbidden_paths:
    - civicclerk/**
    - civiccore/**
    - civic{code,zone,plan,permit,inspect,grants,procure}/**

  release:
    type: tag-and-release
    tag_format: "v{version}"
    requires_lockstep_gate: true
    expected_artifacts:
      - CivicRecordsAI-{version}-Setup.exe
      - CivicRecordsAI-{version}-Setup.exe.sha256
      - release-attestation.json
      - release-attestation.json.bundle

  definition_of_done:
    - civicrecords-ai v1.5.0 released with all 4 artifacts
    - civicrecords-ai main pyproject pins civiccore@v1.0.1
    - All civicrecords-ai CI green on PR
    - Umbrella PR merges with release-lockstep-gate green
    - verify-suite-state.py --remote-only passes for all 26 modules
    - Full-suite installer profile re-enabled in modules.json
    - PCP + queue + handoff durable docs match live state
```

Then run:
```bash
# v1.0 way:
/run resume <run-id>

# v0.5.x way (deprecated shim):
/run-pipeline module-release <run-id>
```

The pipeline orchestrates the 4 phases with human gates at:
- Phase 0 results review (before product work starts)
- Phase 2 rehearsal-ok (before tag push)
- Phase 5 release-ok (before next sprint queues up)

### What happens at each phase

**Phase 0** runs `scripts/preflight_infrastructure.py --module-root <local_path> --repo <repo>`. Checks 1-6 produce a markdown report at `.agent-runs/<run-id>/phase0-report.md`. If any check fails, the agent bundles fixes into ONE PR, waits for merge, re-runs checks until all PASS. Human reviews phase0-report.md before approving Phase 1.

**Phase 1** is the existing executor role with the new self-classification rules embedded. Greps + classifications + edits + local CI. No halt-and-ask on routine classes. The product change ships as ONE PR. CI must pass before merge.

**Phase 2** runs `scripts/local_release_rehearsal.py --module-root <local_path>` which mirrors the release workflow's .env synthesis and runs `verify-release.sh` on fresh docker state. Optionally invokes `act` if available. Produces `.agent-runs/<run-id>/phase2-rehearsal.md`. Human reviews + approves before tag push.

**Phase 3** pushes the tag. Watches the release workflow. If it fails on something Phase 2 didn't catch (truly remote-only), agent halts and asks. Otherwise the workflow passes first try and the GitHub Release publishes. Then opens umbrella PR with release-tag label, waits for release-lockstep-gate green, merges. Opens handoff PR separately (no release-tag).

**Phase 4** is the existing verifier role with the 9-item package: PR/SHA verification three ways, document reads across all four classes (CHANGELOG / browser-QA / handoff / control-plane), drift check, working-tree check, things-the-implementer-didn't-surface review, open items, next directive, recommended next action.

**Phase 5** is the existing manager role with completion-handoff requirements: full tag-move record table (if any moves happened), root-cause analysis of Phase 0 findings, permanent improvements landed, verifier output captured. Updates PCP + queue. Supersedes any PAUSED handoff.

## Expected timing per sprint

| Module type | Old timing | New timing |
|---|---|---|
| Simple module pin bump (audit-team finding, similar shape) | ~30 min (low-debt module) | ~30 min (unchanged — low-debt modules don't benefit much) |
| Migration involving real source changes | ~2-4 hours | ~1.5-2 hours |
| Module with broken release infrastructure | ~8 hours (CivicSuite v1.5.0 receipt) | ~2-3 hours |
| Module needing both source + infrastructure fixes | ~10+ hours | ~3-4 hours |

The biggest wins are on modules with infrastructure debt, which is most modules in the first few sprints of a project. As Phase 0 fixes accumulate, infrastructure debt drops and subsequent sprints are faster.

## What this pipeline does NOT prevent

Three failure classes are intrinsic and the pipeline doesn't claim to eliminate them:

1. **Unknown unknowns** — GitHub Actions runner outages, third-party service deprecations, dependency security advisories landing mid-sprint. Phase 2 catches some; some only surface in CI.

2. **Inter-module integration surprises** — module A's change breaks module B's tests when they share a dependency. Phase 4 verifier catches most; Phase 0 catches the rest.

3. **Agent judgment errors** — rare, but happens. The human gates after Phase 0/2/5 exist for this.

## The honest summary

The pipeline reduces routine failure cascades. It doesn't make a complex project simple. A small human team would still beat the agent on raw clock-time for a multi-module sweep. The agent advantage is when the human team doesn't exist; the cost is the directive overhead.

If a project's owner is exhausted by the friction, the structural answer isn't "make the pipeline better" — it's "reduce the project scope until each sprint fits in one human-attention-window with this overhead."

The pipeline ships as v0.2 today. Future versions will add: pre-built rehearsal harnesses for common stack shapes (Python + Docker, Node + Vite, Rust), `act` integration as a first-class step, and a per-project debt-burndown report so sprint #N shows how much faster it is than sprint #1.

See also:
- `pipelines/module-release.yaml` — pipeline definition
- `pipelines/roles/preflight-auditor.md` — Phase 0 role
- `pipelines/roles/local-rehearsal.md` — Phase 2 role
- `pipelines/self-classification-rules.md` — Phase 1's pre-authorized classifications
- `scripts/preflight_infrastructure.py` — Phase 0 runner
