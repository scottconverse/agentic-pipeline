# Verification

End-to-end test receipts for [agent-pipeline-claude](https://github.com/scottconverse/agent-pipeline-claude). This file's narrative was captured at v1.1.1; the harness has expanded substantially through v1.3.x and v2.0 (hook layer, memory layer, directive contracts). The receipts below document the v1.1.1 baseline; the v2.0 test count is in CHANGELOG.md. Ran on Windows 11 Pro, MSYS Git Bash, Claude Code CLI 2.1.87 / Claude Desktop 2.1.128.

This file documents:
1. The automated test suite (16 tests, ~$0.05 in Haiku for the opt-in cleanroom-smoke).
2. A full end-to-end pipeline run against the `civiccast-shaped` fixture, driven by Sonnet, which exercised all 11 stages and correctly identified real bugs in its own output via the critic + auto-promote layers.

---

## Plugin load state

```
$ claude plugin list
  ❯ agent-pipeline-claude@agent-pipeline-claude
    Version: 1.1.1
    Scope: user
    Status: ✔ enabled

$ claude plugin validate .claude-plugin/plugin.json
✔ Validation passed

$ claude plugin validate .claude-plugin/marketplace.json
✔ Validation passed
```

---

## Static + Unit + Smoke + Cleanroom-load — 15 tests, every commit, $0

```
$ python -m pytest tests/ -q --ignore=tests/test_cleanroom_smoke.py
.................                                                        [100%]
15 passed in 4.77s
```

Breakdown:
- 11 from `test_check_manifest_schema.py` (schema validator unit tests)
- 1 from `test_skill_packaging.py` (smoke — skill self-containment)
- 3 from `test_cleanroom_install.py` (cleanroom-load — `claude --plugin-dir` load test, manifest validate from cleanroom copy, structure check from cleanroom copy)

---

## Cleanroom-smoke — 1 test, opt-in, ~$0.05 in Haiku, ~48s wall

```
$ ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_cleanroom_smoke.py -v -m cleanroom_e2e
tests/test_cleanroom_smoke.py::test_cleanroom_pipeline_init_scaffolds_into_real_project PASSED [100%]
1 passed in 48.48s
```

What this test actually exercises:
- Copies the plugin source to `tmp_path/plugin/` (no `.git`, no caches, no host config).
- Scaffolds a minimum-viable fixture project to `tmp_path/project/` (README.md + CLAUDE.md + HANDOFF.md + empty `docs/`).
- Runs `claude --plugin-dir <plugin> -p` from the fixture, two turns: invoke `agent-pipeline-claude:pipeline-init`, then `APPROVE`.
- Asserts `.pipelines/` actually scaffolded into the fixture, with 9 expected role files + 5 pipeline yamls + 5 policy scripts.

This is the layer that catches "plugin loads but skills no-op" — a failure class the load-only cleanroom test would miss. v1.1.0 had the load-only test; v1.1.1 closes the gap.

---

## End-to-end pipeline run (Sonnet, against `civiccast-shaped` fixture)

The full `/run` on a real model is the only test that exercises orchestration: stage spawning, role-file format compliance, auto-promote gating, manager decision quality. Cost: ~$15 in Sonnet, ~35 minutes wall.

Procedure:

```bash
# Copy fixture to a clean workdir (avoid polluting tests/fixtures/)
mkdir -p /tmp/sonnet-e2e
cp -r tests/fixtures/civiccast-shaped/* /tmp/sonnet-e2e/

cd /tmp/sonnet-e2e
SID=$(python -c "import uuid; print(uuid.uuid4())")

export ANTHROPIC_API_KEY=sk-ant-...

# 4 turns, all Sonnet
claude -p --session-id "$SID" --model sonnet \
  'Use the Skill tool to invoke "agent-pipeline-claude:pipeline-init".'
claude -p --resume "$SID" --model sonnet 'APPROVE'

claude -p --resume "$SID" --model sonnet \
  'Use the Skill tool to invoke "agent-pipeline-claude:run" with task: close QA-005 conflict-409 race.'
claude -p --resume "$SID" --model sonnet \
  'APPROVE the manifest. Proceed with the pipeline run. ... STRICTLY follow the role file format requirements ... At the manager gate, STOP (do not actually merge anything).'
```

### Result: pipeline ran every stage; manager correctly BLOCKED

`run.log` (proves stage-by-stage append works when the model honors the spec — Haiku skipped this, Sonnet didn't):

```
2026-05-12T21:26:27Z STAGE_DONE manifest      artifact=manifest.yaml             bytes=6045
2026-05-12T21:30:22Z STAGE_DONE research      artifact=research.md               bytes=31677
2026-05-12T21:34:05Z STAGE_DONE plan          artifact=plan.md                   bytes=28541
2026-05-12T21:38:52Z STAGE_DONE test-write    artifact=failing-tests-report.md   bytes=8535
2026-05-12T21:45:47Z STAGE_DONE execute       artifact=implementation-report.md  bytes=22328
2026-05-12T21:46:18Z STAGE_DONE policy        artifact=policy-report.md          bytes=1377
2026-05-12T21:49:50Z STAGE_DONE verify        artifact=verifier-report.md        bytes=25812
2026-05-12T21:53:36Z STAGE_DONE drift-detect  artifact=drift-report.md           bytes=26527
2026-05-12T21:59:10Z STAGE_DONE critique      artifact=critic-report.md          bytes=34784
2026-05-12T21:59:21Z STAGE_DONE auto-promote  artifact=auto-promote-report.md    bytes=1081
2026-05-12T22:00:52Z STAGE_DONE manager       artifact=manager-decision.md       bytes=9147
```

### All 13 expected artifacts produced

```
.agent-runs/2026-05-12-close-qa-005-conflict-409-race/
├── auto-promote-report.md     1.1 KB
├── critic-report.md          34.8 KB
├── draft-provenance.md        3.5 KB
├── drift-report.md           26.5 KB
├── failing-tests-report.md    8.5 KB
├── implementation-report.md  22.3 KB
├── manager-decision.md        9.1 KB
├── manifest.yaml              6.0 KB
├── notes/                    (drift-detector pre-edit snapshots)
│   ├── pre-edit-CHANGELOG.md.md
│   ├── pre-edit-router.py.md
│   └── pre-edit-store.py.md
├── plan.md                   28.5 KB
├── policy-report.md           1.4 KB
├── research.md               31.7 KB
├── run.log                    0.9 KB
└── verifier-report.md        25.8 KB
```

### Strict-format compliance verified

The auto-promote regex requires exact summary lines. Sonnet produced them verbatim:

```
$ grep -E '^\*\*Findings|^\*\*Drift|^\*\*Criteria|^POLICY' .agent-runs/*/*.md

critic-report.md:    **Findings: 11 total, 4 blocker, 1 critical, 4 major, 2 minor**
drift-report.md:     **Drift: 3 total, 2 blocker**
verifier-report.md:  **Criteria: 10 total, 7 MET, 1 PARTIAL, 2 NOT MET, 0 NOT APPLICABLE**
drift-report.md:     **Criteria: 10 total, 6 MET, 2 PARTIAL, 2 NOT MET, 0 NOT APPLICABLE**
```

### Auto-promote evaluated the 6 conditions correctly

```
# auto-promote — NOT_ELIGIBLE

- FAIL  verifier-clean    — verifier reports 2 NOT MET and 1 PARTIAL criterion(a)
- FAIL  critic-clean      — critic reports 4 blocker and 1 critical finding(s)
- FAIL  drift-clean       — drift-detector reports 2 blocker drift item(s)
- FAIL  policy-passed     — policy-report.md does not contain `POLICY: ALL CHECKS PASSED`
- PASS  judge-clean       — judge layer was not active for this run
- FAIL  tests-passed      — implementation-report.md does not contain recognizable test-pass signal
```

### Critic found four real blockers in the fixture work

This is the load-bearing evidence: **the pipeline catches its own work's bugs**.

- **B-1 (SERIALIZABLE gap on `session` path).** `civiccast/schedule/store.py` lines 326–329: `if session is not None: return await _update_metadata_in_session(...)` — no `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` before that call. The TOCTOU window the run was supposed to close is structurally open for all test-driven invocations.
- **B-2 (structurally impossible test).** `TestRouterConflictMapping::test_schedule_conflict_error_returns_409_with_conflicting_item` asserts HTTP 409 with `conflicting_item` in the body. The PATCH route calls `update_metadata`, which raises `AssetAlreadyPublishedError`, not `ScheduleConflictError`. The test cannot produce that 409 via any executable path. Fix: `dependency_overrides` in the test client fixture.
- **B-3 (Python 3.12+ asyncio incompatibility).** `tests/schedule/test_real_postgres.py:116` uses `asyncio.get_event_loop().run_until_complete()`. Python 3.12 raises `RuntimeError('There is no current event loop in thread MainThread')`. The manifest tooling spec mandates Python 3.12+. Replace with `asyncio.run()`.
- **B-4 (no live test/lint/type output).** `implementation-report.md:94` says "Tests would pass: ImportError resolves when store.py and router.py exist." Global `CLAUDE.md` §Verification: *"'It should work' is not evidence."* The fixture environment can't satisfy this; flagged for live-env follow-up.

### Manager decision: BLOCK

```
Decision: BLOCK

Verifier: NOT MET on 2 criteria, no director-decision-authorized deferral.
Critic:   4 blocker findings (B-1, B-2, B-3, B-4 above).
Drift:    2 blocker drift items.

Per role rules: "Do not say PROMOTE if the verifier said NOT MET on any criterion."
Manager cited every finding with specific file:line evidence.
```

This is the system working exactly as designed.

---

## Comparison: Haiku vs Sonnet on the same fixture run

Both models successfully ran the full pipeline against `civiccast-shaped`. The difference is in adherence to the strict-format spec and the analytical depth:

| | Haiku 4.5 | Sonnet 4.6 |
|---|---|---|
| Wall time | ~5 min | ~35 min |
| API cost (rough) | ~$2 | ~$15 |
| Stages completed | 11 ✓ | 11 ✓ |
| `run.log` lines | 1 (manifest only) | 11 (every stage) |
| `**Findings: N total, ...**` summary line | missing | present, exact format |
| `**Criteria: N total, ...**` summary line | missing | present |
| `**Drift: N total, ...**` summary line | missing | present |
| Auto-promote determination | NOT_ELIGIBLE (all 5 checks failed on format-drift) | NOT_ELIGIBLE (legitimate substantive failures) |
| Manager decision | PROMOTE (incorrect — work has real bugs) | BLOCK (correct — 4 real blockers found) |

**Takeaway.** The plugin's strict format spec assumes a model that can faithfully produce structured markdown. Haiku approximates; Sonnet conforms. The fall-back-to-manager-gate behavior is the safety net: when auto-promote can't machine-verify, the human gate always engages, and the manager role can still cite specific evidence — but the quality of that citation depends on the model.

For routine runs Haiku is fine and cheap; for runs where you want eligibility checks to be meaningful, use Sonnet (`--model claude-sonnet-4-6` at session start).

---

## What this run did NOT prove (and the followups)

- **`run.log` is still single-line under Haiku.** Role files don't mandate the `STAGE_DONE` append — they describe it but stop short of REQUIRE. v1.x followup: tighten the role-file spec so even weaker models honor it. The plugin design assumes capable models; that's worth documenting more loudly.
- **No live `pytest`/`ruff`/`mypy` run.** The fixture project has no real source code — executor wrote stubs, verifier acknowledged tests don't actually pass. A real consumer project would have these tools available and the pipeline would invoke them. Out of scope for fixture E2E.
- **Judge layer not exercised.** The optional judge layer fires on `external_facing` or `high_risk` actions. This run didn't trigger any. Separate fixture would be needed to exercise.

---

## Reproducing this verification

```bash
# Prereqs: claude CLI, ANTHROPIC_API_KEY, ~$15 of API budget for Sonnet
# (or ~$2 for Haiku — but Sonnet is what we documented above)

git clone https://github.com/scottconverse/agent-pipeline-claude.git
cd agent-pipeline-claude

# 1. Static + unit + smoke + cleanroom-load (per-commit tier, $0)
python -m pytest tests/ -q --ignore=tests/test_cleanroom_smoke.py
# Expected: 15 passed

# 2. Cleanroom-smoke (opt-in, ~$0.05)
ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_cleanroom_smoke.py -m cleanroom_e2e
# Expected: 1 passed in ~50s

# 3. Full E2E against the fixture (manual, ~$15 Sonnet)
mkdir /tmp/e2e-fixture
cp -r tests/fixtures/civiccast-shaped/* /tmp/e2e-fixture/
cd /tmp/e2e-fixture
SID=$(python -c "import uuid; print(uuid.uuid4())")
export ANTHROPIC_API_KEY=sk-ant-...

claude -p --session-id "$SID" --model sonnet \
  'Use the Skill tool to invoke "agent-pipeline-claude:pipeline-init".'
claude -p --resume "$SID" --model sonnet 'APPROVE'
claude -p --resume "$SID" --model sonnet \
  'Use the Skill tool to invoke "agent-pipeline-claude:run" with task: close QA-005 conflict-409 race.'
claude -p --resume "$SID" --model sonnet \
  'APPROVE the manifest. Proceed with the pipeline run. STRICTLY follow role file format requirements. At the manager gate, STOP.'

# Inspect
ls .agent-runs/<run-id>/                    # all 13 artifacts present
cat .agent-runs/<run-id>/run.log            # 11 stage timestamps
cat .agent-runs/<run-id>/auto-promote-report.md
cat .agent-runs/<run-id>/manager-decision.md
```

If your output differs materially from the receipts above, file an issue with the run-log and manager-decision attached.

---

## Receipts

Raw outputs from this verification run are preserved at `/tmp/sonnet-e2e/.agent-runs/2026-05-12-close-qa-005-conflict-409-race/` on the machine that produced this doc. Inline excerpts above are verbatim quotes from those artifacts.

| Artifact | Size | Key content |
|---|---|---|
| `manifest.yaml` | 6.0 KB | 13/13 fields auto-derived from `docs/releases/v0.4-scope-lock.md` + `docs/research/v04-slice1-broadcast-spine-design.md` + `HANDOFF.md` |
| `research.md` | 31.7 KB | All director notes developed, 2 additional DQs surfaced |
| `plan.md` | 28.5 KB | 6-file scope, SERIALIZABLE dual-fix strategy, director-decision bindings |
| `critic-report.md` | 34.8 KB | 11 findings (4 blocker / 1 critical / 4 major / 2 minor) |
| `manager-decision.md` | 9.1 KB | BLOCK decision with file:line citations for every finding |
