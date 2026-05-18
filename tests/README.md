# tests/

Test surface for `agent-pipeline-claude` v2.0.0+.

## Testing taxonomy

Six layers, ordered by cost and depth:

| Layer | What it proves | Runtime | API spend | When it runs |
|---|---|---|---|---|
| **Static** | Manifests well-formed; shell/Python syntax OK | ms | $0 | every commit (CI) |
| **Unit** | Policy scripts behave correctly on synthetic input | seconds | $0 | every commit (CI) |
| **Smoke (in-tree)** | Plugin's skills are self-contained when copied to an installed-cache shape | seconds | $0 | every commit (CI) |
| **Cleanroom — load** | Plugin loads from a fresh copy (no `.git`, no caches, no host config) via `claude --plugin-dir` | seconds | $0 | every commit (CI) |
| **Scaffold unit** | Deterministic copy-the-payload step lands the expected `.pipelines/` + `scripts/policy/` tree | sub-second | $0 | every commit (CI) |
| **Cleanroom — smoke** | The FIRST skill actually executes against a fresh fixture (model + CLI + Skill-tool, end-to-end) | ~60s | ~$0.05 (Haiku) | opt-in / pre-release |
| **End-to-end** | Full `/run` orchestrates research → … → manager against a fixture | minutes | ~$2 Haiku / ~$15 Sonnet | tags + nightly |

Each layer catches a different failure class. **Cleanroom-load** catches the v1.0.0/v1.0.1 regression (manifest passed unit tests but loader silently rejected the install). **Cleanroom-smoke** is the layer that catches one tier up: plugin loads but skills no-op, scaffold to the wrong path, or hang at orientation. The v1.1.0 cleanroom-load test ships proved the plugin loads; v1.1.1's cleanroom-smoke test proves the plugin **works**.

### Static

- **Manifest validation:** `claude plugin validate .claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` must both report `✔ Validation passed`.
- **Plugin structure check:** `python tests/check_plugin_structure.py` — counts skills, commands, roles, pipelines and confirms the install-shape is intact.
- **Skill packaging check:** `python scripts/check_skill_packaging.py` — recursively scans every `*.md` under each skill folder, flags repo-root path references (`pipelines/...`, `scripts/...`) that won't resolve from an installed-cache copy. Discriminating regex skips legitimate consumer-project paths (`docs/`, `tests/`, `CLAUDE.md`).

### Unit

- **`tests/test_check_manifest_schema.py`** — the schema validator's pass/fail surface on synthetic manifests. Covers version reporting, error message format, multiple-violations handling. Highest-leverage Python target because the validator gates every run's manifest.
- Other policy scripts (`check_allowed_paths.py`, `check_no_todos.py`, `check_adr_gate.py`, `auto_promote.py`) ship with minimal coverage — tests are TODO for v1.x. The schema validator is the load-bearing one.

### Smoke

- **`tests/test_skill_packaging.py`** — wraps `scripts/check_skill_packaging.py` in pytest, verifies the plugin's skills are self-contained when copied into an installed-cache shape.

Smoke + Static together catch "manifest valid in isolation, but loader rejects layout" regressions.

### Cleanroom — load (per commit, $0)

- **`tests/test_cleanroom_install.py`** — three tests against a fresh copy of the plugin in an isolated `tmp_path/agent-pipeline-claude/` (no `.git`, no `__pycache__`, no `.agent-runs/`, no `installed_plugins.json` entries):
  1. `test_cleanroom_install_loads_via_plugin_dir` — runs `claude --plugin-dir <copy> plugin list` and asserts the plugin shows up with `Status: ✔ loaded` and the manifest-declared version.
  2. `test_cleanroom_install_validates` — runs `claude plugin validate` against both manifests in the cleanroom copy.
  3. `test_cleanroom_install_structure_check` — runs `check_plugin_structure.py` from inside the cleanroom copy as cwd.

These three skip gracefully if `claude` isn't on PATH (via `pytest.skip`). They catch the v1.0.0 / v1.0.1 schema-rejection regression class — manifest validates in isolation but the loader rejects the layout.

**Honest gap (v1.1.0 had this; v1.1.1 closes it):** load-only cleanroom proves the plugin shows `✔ loaded` and the manifest validates. It does NOT prove any skill actually does anything. That's what the next tier is for.

### Scaffold unit test (v1.1.2+, $0, sub-second)

- **`tests/test_scaffold_pipeline.py`** — 9 tests against `scripts/scaffold_pipeline.py`, the deterministic copy-the-bundled-payload step that `/pipeline-init` performs on APPROVE. Asserts the same `.pipelines/` + `scripts/policy/` file tree that the cleanroom-smoke test asserts, but executes the copy as a plain Python function call against a tmp_path — no `claude` CLI, no model, $0, sub-second.

This catches the *deterministic* sub-class of the failure cleanroom-smoke watches for: payload drift, missing files in the bundle, broken copy logic. The *non-deterministic* sub-class (model fails to invoke the skill, claude CLI's Skill-tool dispatch silently no-ops, markdown unreadable) still requires cleanroom-smoke. Both tests share the same expected-file list — if you add a file to the bundled payload, update both assertions.

See [`docs/LOCAL_TEST_RESEARCH.md`](../docs/LOCAL_TEST_RESEARCH.md) for why this tier was added (local-LLM proxy walls).

### Cleanroom — smoke (opt-in, ~$0.05, ~60s)

- **`tests/test_cleanroom_smoke.py`** — `@pytest.mark.cleanroom_e2e`:
  - Copies the plugin source to `tmp_path/plugin/` (no `.git`, no caches).
  - Scaffolds a minimum-viable fixture project to `tmp_path/project/` (README.md + CLAUDE.md + HANDOFF.md + empty `docs/`).
  - Runs `claude --plugin-dir <tmp_path/plugin> -p` from the fixture, two turns: invoke `agent-pipeline-claude:pipeline-init`, then `APPROVE`.
  - Asserts the fixture now contains `.pipelines/` with 9 expected role files + 5 pipeline yamls + 5 policy scripts.

This is the layer that catches "plugin loads but skills no-op" and "skills scaffold to the wrong path" — failures the load-only tests cannot see. Cost: ~$0.05 in Haiku, ~60s wall. Requires `ANTHROPIC_API_KEY` in env and `claude` on PATH; skips otherwise.

Run with:

```bash
ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_cleanroom_smoke.py -m cleanroom_e2e
```

Or run the whole suite (this test skips without an API key):

```bash
ANTHROPIC_API_KEY=sk-ant-... pytest tests/
```

### End-to-end

E2E exercises `/run` against a real Claude Code session with a real model. The plugin is a Python-and-Markdown orchestrator for an LLM-driven pipeline; the only complete verification is actually running a pipeline.

`tests/fixtures/` holds two fixture projects:

- **`civiccast-shaped/`** — stripped-down replica of a CivicCast-style project: one-line spec, release plan, per-rung scope-lock, design note, CLAUDE.md, sample HANDOFF.md. Drafter should produce a manifest referencing the scope-lock and design doc.
- **`greenfield/`** — empty directory. Drafter should return `NO_SPEC_FOUND` and write a minimal-skeleton manifest.

E2E procedure (manual or CI-on-tag):

```bash
# Prereqs: claude CLI on PATH, ANTHROPIC_API_KEY set,
# plugin enabled (`claude plugin list` reports ✔ enabled)

cd tests/fixtures/civiccast-shaped/
SID=$(python -c "import uuid; print(uuid.uuid4())")

# Step 1: pipeline-init (two-turn: invoke + APPROVE)
claude -p --session-id "$SID" --model haiku \
  'Use the Skill tool to invoke "agent-pipeline-claude:pipeline-init".'
claude -p --resume "$SID" --model haiku 'APPROVE'

# Step 2: /run draft manifest
claude -p --resume "$SID" --model haiku \
  'Use the Skill tool to invoke "agent-pipeline-claude:run" with task: close QA-005 conflict-409 race.'

# Step 3: APPROVE manifest; pipeline executes
claude -p --resume "$SID" --model haiku 'APPROVE'

# Inspect produced artifacts
ls .agent-runs/<run-id>/
```

Expected artifacts in `.agent-runs/<run-id>/`:
- `manifest.yaml`, `draft-provenance.md`
- `research.md`, `plan.md`
- `implementation-report.md`, `policy-report.md`
- `verifier-report.md`, `failing-tests-report.md`
- `drift-report.md`, `critic-report.md`
- `auto-promote-report.md`, `manager-decision.md`
- `run.log`

**E2E is NOT yet pytest-driven.** It requires API spend (~$2/run with Haiku, ~$20/run with Sonnet) and an interactive multi-turn driver. A future `tests/test_e2e_fixture_run.py` could automate this against `--model haiku` for tag CI, but for v1.1.0 it's manual and the cleanroom tier handles the per-commit automated layer.

**Fixture-pollution caution.** Running `/run` against `tests/fixtures/civiccast-shaped/` writes stub source code, tests, and `.agent-runs/` into the fixture (the executor stage materializes code that matches the manifest's `allowed_paths`). Before re-running the test suite afterwards, clean the fixture:

```bash
git clean -fdx tests/fixtures/civiccast-shaped/
git checkout tests/fixtures/civiccast-shaped/
```

Otherwise pytest will try to collect the executor's generated `tests/schedule/*.py` and fail on import.

### What deliberately is NOT tested

- **Role-file content** (`pipelines/roles/*.md`). These are spec-style markdown for LLM consumption. The "test" is running a fixture-E2E pipeline against the role and inspecting the artifact. No useful unit assertion exists.
- **Manager decision quality.** Same reason — LLM judgment is inspected manually or audited via critic/drift role outputs.
- **Real-world project integration** beyond the fixtures. Each project type has its own shape; we test the plugin, not every consumer.

## Running

```bash
pip install pytest
python -m pytest tests/ -v       # all static, unit, smoke, cleanroom
python -m pytest tests/test_cleanroom_install.py -v  # cleanroom only
```

## Adding tests

For a new policy script:
1. Drop `tests/test_check_<name>.py` mirroring `test_check_manifest_schema.py`.
2. Cover at least one pass + one fail + one edge case.

For a new fixture:
1. `tests/fixtures/<name>/` with a `README.md` explaining its shape.
2. Document expected drafter / executor behavior in this README.

For a new test tier (e.g., automated E2E):
1. Add a `tests/test_<tier>_*.py` module.
2. If it requires external resources (`claude` CLI, API key), gracefully skip via `pytest.skip` when unavailable.
3. Document the tier in the table at the top of this README.
