# Architecture

> **v2.0 update (2026-05-17):** Two architectural layers added on top of the v1.3 pipeline. See "v2.0 architecture extensions" below. The original v1.x architecture (gates, role files, judge layer, auto-promote) is preserved unchanged.

## v2.0 architecture extensions

### Layer 1: Eleven Cowork lifecycle hooks (`hooks/`)

```mermaid
flowchart LR
    Cowork[Cowork session events] --> Runner["hooks/hook_runner.py"]
    Runner --> SS[handle_session_start]
    Runner --> UPS[handle_user_prompt_submit]
    Runner --> PreT[handle_pre_tool_use]
    Runner --> PermR[handle_permission_request]
    Runner --> PostT[handle_post_tool_use]
    Runner --> PostF[handle_post_tool_use_failure]
    Runner --> PreC[handle_pre_compact]
    Runner --> PostC[handle_post_compact]
    Runner --> SubS[handle_subagent_stop]
    Runner --> Stop[handle_stop]
    Runner --> SE[handle_session_end]

    SS & UPS & PreT & PermR & PostT & PostF & PreC & PostC & SubS & Stop & SE --> Mem["record_hook_memory()"]
    Mem --> LayerA[".agent-runs/<run-id>/memory/*.jsonl"]
    Mem --> Handoff[handoff_current.md]
```

Hooks fire from Cowork's runtime via `hooks/hooks.json`. Each handler:

1. Resolves the project root from `$CLAUDE_PROJECT_DIR` (Cowork roots cwd at `.klodock`; the env var is the right answer).
2. Discovers active runs by walking `.agent-runs/*/active-control-state.md`.
3. Decides on the event (block, deny, warn, allow, inject context, record memory).
4. Calls `record_hook_memory()` which appends to the right `*.jsonl` and regenerates `handoff_current.md`.

The PostCompact handler re-injects `handoff_current.md` as `additionalContext` — the load-bearing mechanism that makes pipeline state survive context compaction.

### Layer 2: Two-layer memory (`memory/`)

```mermaid
flowchart TB
    Hooks[Phase 4 hooks] -->|always| LayerA[".agent-runs/<run-id>/memory/*.jsonl<br/>Layer A: file-backed, unconditional"]
    SyncCmd["pipeline mem0 sync"] -->|reads typed records| LayerA
    SyncCmd -->|policy.add| Policy[PolicyLayer]
    Policy -->|FR-7 type check| Policy
    Policy -->|FR-11 redact| Policy
    Policy -->|FR-6 scope| Policy
    Policy -->|FR-13 breaker| Adapter
    Adapter -->|platform or oss| LayerB["Layer B: Mem0<br/>(best-effort, cross-session)"]
    Policy -.->|on breaker open| Outbox[".mem0/outbox/*.json"]
```

**Layer A** is the safety floor: every hook writes here. No network, no docker, no SDK. Always works.

**Layer B** is the cross-session bridge: typed records (those with `metadata.type` in the closed taxonomy `{decision, task_learning, anti_pattern, user_preference, environmental, convention, session_state}`) get forwarded to Mem0 via `pipeline mem0 sync`. The `PolicyLayer` enforces every PRD FR (scoping, taxonomy, budget, latency, redaction, circuit breaker, consent) before any backend call.

Identity follows PRD §5.2:

```text
user_id  = sha256(git user.email)[:16]    # stable, no PII leak
agent_id = "claude-code"                  # fixed
app_id   = slug(git remote get-url origin) # repo-scoped
run_id   = "{branch}-{short-sha}-{epoch}" # one per agent task
```

### Layer 3: Directive contract data flow

```mermaid
flowchart LR
    D["directive.yaml"] --> H["SHA-256 hash"]
    H --> L["run.log directive-bound line<br/>(only after conformance passes)"]
    D --> C1["check_directive_conformance.py"]
    M["manifest.yaml"] --> C1
    S["scope-lock.yaml"] --> C1
    C1 -->|"exact match"| MG["manifest gate auto-complete"]
    C1 -->|"mismatch (never bound)"| MI["manifest gate stays interactive"]
    C1 -->|"mismatch after bind"| STOP["exit 3 CONTRACT_DIVERGED<br/>orchestrator STOP"]
    D --> C2["check_plan_against_directive.py"]
    P["plan.md"] --> C2
    C2 -->|all assertions pass| PG["plan gate auto-complete"]
    C2 -->|any fails| PI["plan gate stays interactive"]
    C2 -->|"manifest re-verify fails"| PSTOP["exit 2 CONTRACT_DIVERGED"]
    D --> AP["auto_promote.py"]
    Stack["verifier + critic + drift + policy + judge + tests"] --> AP
    AP -->|"six base + N directive green"| MD["manager-decision.md<br/>PROMOTE with directive citation"]
```

All PR #5 amendments from the codex side are present:

- **Bind-after-conformance**: the `directive-bound` line is written only after manifest AND scope-lock both match.
- **Append-not-prepend**: the binding line is appended to `run.log`, preserving append-only invariant.
- **Exit 3 CONTRACT_DIVERGED**: when bound + diverged on resume, the orchestrator STOPS rather than silently falling through.
- **Downstream re-verify**: `auto_promote.py::_check_directive_manager` and `check_plan_against_directive.py` both re-verify manifest/scope conformance — the binding can't be the only proof.

---

# v1.x Architecture (preserved through v2.0)

How the agent-pipeline-claude plugin is organized, what runs where, and which
artifact each stage produces.

**Current version: v2.0.0.** v2.0 ("heavier-hand") adds three layers on top of the v1.3.x architecture documented below — an eleven-event Cowork lifecycle hook layer, directive-contract pre-approval with bind-after-conformance, and a Mem0 cross-session memory layer — without removing any of the v1.x gates. v1.3.x replaced the original chat-APPROVE ceremony with `AskUserQuestion` modal prompts (one click each) for the three human gates (manifest, plan, manager); v2.2.1 reverses that experiment after the operator-UX failure (Cowork's modal overlay hid chat context at gate-decision time) and restores chat-based gates with a deterministic first-token keyword grammar (`APPROVE` / `REVISE` / `REPLAN` / `BLOCK` / `VIEW`, case-insensitive). The modal-budget hook denies every `AskUserQuestion` during an active non-drafting run. v1.1 fixed the install/runtime adapter that v1.0.0–v1.0.2 got wrong (one layout, namespaced invocation, validating marketplace manifest, self-contained skills) without changing pipeline behavior. v1.0 rebuilt the user-facing surface around four load-bearing decisions (Cowork-first, spec-aware drafting, one slash skill, chat-native gates) while preserving every v0.5 hardening mechanism intact. This document describes the v1.x stage architecture that v2.0 still rides on top of; the v2.0 hook/memory/directive layers are detailed in the v2.0 CHANGELOG entry.

This document is for two audiences:

1. **Operators** who want to understand what the plugin does on their
   machine before they trust it with a real codebase.
2. **Contributors** who want to add a new pipeline type, a new role, or a
   new policy check without breaking the contract the rest of the system
   depends on.

If you only want to run a pipeline, read [`USER-MANUAL.md`](USER-MANUAL.md)
first. This document assumes you have already done at least one run.

---

## 1. The big picture

The plugin orchestrates work across **three layers**:

1. **Plugin layer** (`agent-pipeline-claude/`) — the slash commands, the
   stage definitions, the role files, and the policy scripts. Versioned,
   shared across all your projects.
2. **Project layer** (`<your-project>/`) — copies of the pipeline
   definitions, role files, and policy scripts that `/pipeline-init`
   scaffolded into your project. Yours to customize.
3. **Run layer** (`<your-project>/.agent-runs/<run-id>/`) — one directory
   per pipeline run, containing the manifest, every produced artifact,
   and the append-only `run.log`. Gitignored by default.

```mermaid
flowchart TB
    subgraph PluginLayer["Plugin layer (one install per machine)"]
        direction LR
        A1[".claude-plugin/plugin.json"]
        A2["skills/<br/>pipeline-init/SKILL.md<br/>run/SKILL.md<br/>audit-init/SKILL.md"]
        A3["pipelines/<br/>feature.yaml<br/>bugfix.yaml<br/>roles/*.md"]
        A4["scripts/<br/>check_*.py<br/>run_all.py"]
    end

    subgraph ProjectLayer["Project layer (per repo, after /pipeline-init)"]
        direction LR
        B1["CLAUDE.md"]
        B2[".pipelines/<br/>copies of YAMLs<br/>copies of roles/"]
        B3["scripts/policy/<br/>copies of check scripts"]
        B4[".gitignore<br/>(adds .agent-runs/)"]
    end

    subgraph RunLayer["Run layer (per pipeline invocation)"]
        direction LR
        C1[".agent-runs/&lt;run-id&gt;/<br/>manifest.yaml<br/>research.md<br/>plan.md<br/>...<br/>manager-decision.md<br/>run.log"]
    end

    PluginLayer -- "/pipeline-init copies into" --> ProjectLayer
    ProjectLayer -- "/agent-pipeline-claude:run produces" --> RunLayer
```

The strict separation matters: when an agent stage runs, it only sees the
project layer and the run layer. The plugin layer is read-only template
material; once scaffolded, your project's behavior is yours.

### v1.0 → v1.1 surface change: one namespaced skill, drafted manifest

v0.5.2 exposed three slash commands: `/pipeline-init`, `/new-run`, `/run-pipeline`. The user hand-authored 11 manifest fields between `/new-run` and `/run-pipeline`.

v1.0 collapsed the run-time surface to one command: `/run "<short description>"`. Before the first pipeline stage executes, `/run` spawns a **manifest-drafter** subagent (`.pipelines/roles/manifest-drafter.md`). The drafter walks the project root for spec, release-plan, scope-lock, design-note, ADR, `CLAUDE.md`, and ledger artifacts, then writes a populated `manifest.yaml` plus a `draft-provenance.md` audit trail. The user reviews the drafted YAML in chat and replies `APPROVE` to start the pipeline.

v1.1 fixes the install adapter v1.0 got wrong:

- **Plugin skills are namespaced.** Per the [Claude Code plugin docs](https://code.claude.com/docs/en/plugins), all marketplace plugin skills are invoked as `/<plugin-name>:<skill-name>`. The bare `/run` form documented in v1.0 was never reachable. The canonical form is `/agent-pipeline-claude:run`.
- **One layout (`skills/`).** v1.0.1 added a `skills/` mirror alongside `commands/`, causing every skill to register twice. v1.1 removes `commands/` entirely. Each skill is `skills/<name>/SKILL.md` (thin shim with frontmatter + tool mapping) plus `skills/<name>/references/<name>.md` (canonical procedure).
- **Marketplace manifest validates.** `marketplace.json` no longer carries an unrecognized root `description`; it lives under `metadata`.
- **Deprecated shims removed.** `/new-run` and `/run-pipeline` were marked deprecated in v1.0 for v1.1 removal. They are now gone.

The mechanism downstream of the manifest gate is unchanged from v0.5.2.

---

## 2. Stage flow — feature pipeline

The default `feature` pipeline runs eight stages in order. Three of them
are **human-approval gates** (orange). One is an **automated policy
gate** (yellow). The rest are agent stages (blue) that delegate to a
fresh subagent per stage.

```mermaid
flowchart TB
    Start([User runs /run &quot;short description&quot;]) --> D[manifest-drafter<br/>role: pre-stage subagent<br/>reads project spec/release-plan/scope-lock]
    D -- draft --> M[manifest gate<br/>role: human<br/>chat keyword reply: APPROVE / REVISE / VIEW]
    M -- APPROVE --> R[research<br/>role: researcher<br/>artifact: research.md]
    R --> P[plan<br/>role: planner<br/>artifact: plan.md]
    P -- APPROVE --> TW[test-write<br/>role: test-writer<br/>artifact: failing-tests-report.md]
    TW --> E[execute<br/>role: executor<br/>artifact: implementation-report.md]
    E --> POL[policy<br/>role: pipeline<br/>command: scripts/policy/run_all.py<br/>artifact: policy-report.md]
    POL -- exit 0 --> V[verify<br/>role: verifier<br/>artifact: verifier-report.md]
    V --> MGR[manager<br/>role: manager<br/>artifact: manager-decision.md]
    MGR -- APPROVE --> Done([Pipeline complete])

    M -. BLOCKED .-> Stop1([Stop])
    P -. BLOCKED .-> Stop2([Stop])
    POL -. exit != 0 .-> Stop3([Stop])
    MGR -. BLOCK or REPLAN .-> Stop4([Stop])

    classDef human fill:#ffd9b3,stroke:#cc6600,color:#000
    classDef agent fill:#cce5ff,stroke:#0066cc,color:#000
    classDef policy fill:#fff3b3,stroke:#999900,color:#000
    classDef stop fill:#ffb3b3,stroke:#cc0000,color:#000

    class M,P,MGR human
    class R,TW,E,V agent
    class POL policy
    class Stop1,Stop2,Stop3,Stop4 stop
```

The `bugfix` pipeline collapses test-write and execute into a single
**reproduce → patch** sequence, but the gate structure is identical:
manifest gate at the start, plan gate after research, manager gate at
the end.

---

## 3. Artifact data flow

Each stage reads every prior artifact and writes exactly one new one.
This is what makes the pipeline resumable — at any point, the run
directory is the complete state.

```mermaid
flowchart LR
    subgraph Inputs["Stage inputs"]
        I0["manifest.yaml<br/>(human)"]
    end

    I0 --> R["research.md<br/>(researcher)<br/>+ surfaces director<br/>decisions"]
    I0 --> P["plan.md<br/>(planner)<br/>uses research +<br/>director choices"]
    R --> P
    I0 --> TW["failing-tests-report.md<br/>(test-writer)<br/>tests written, all RED"]
    R --> TW
    P --> TW
    I0 --> E["implementation-report.md<br/>(executor)<br/>code, tests now GREEN"]
    R --> E
    P --> E
    TW --> E
    E --> JL["judge-log.yaml<br/>judge-metrics.yaml<br/>(orchestrator, v0.4)<br/>per-action records<br/>(only when judge<br/>layer is enabled)"]
    E --> POL["policy-report.md<br/>(automated)<br/>allowed_paths,<br/>no TODOs, ADRs"]
    POL --> V["verifier-report.md<br/>(verifier)<br/>independent check vs.<br/>manifest exit criteria"]
    I0 --> V
    R --> V
    P --> V
    TW --> V
    E --> V
    JL --> V
    V --> MGR["manager-decision.md<br/>(manager)<br/>PROMOTE / BLOCK / REPLAN<br/>cites verifier verbatim"]
    POL --> MGR
    JL --> MGR
    I0 --> MGR

    classDef human fill:#ffd9b3,stroke:#cc6600,color:#000
    classDef agent fill:#cce5ff,stroke:#0066cc,color:#000
    classDef policy fill:#fff3b3,stroke:#999900,color:#000
    classDef judge fill:#ccf2cc,stroke:#339933,color:#000
    class I0 human
    class R,P,TW,E,V,MGR agent
    class POL policy
    class JL judge
```

Two important properties of this flow:

- **Append-only.** No stage modifies a prior artifact. The verifier reads
  the executor's report; it does not edit it.
- **Manager has full context.** The PROMOTE/BLOCK/REPLAN decision is made
  by an agent that has read everything and must cite verifier evidence
  verbatim. It cannot be polite or encouraging — the role file forbids
  it.

---

## 4. The three human gates

Every gate uses the same pattern: the prior stage produces an artifact,
the orchestrator prints a chat prompt with the recognized keyword grammar
(`APPROVE` / `REVISE` / `REPLAN` / `BLOCK` / `VIEW`, case-insensitive),
pauses, and parses the first non-whitespace token of the operator's
reply. There is no "approve with caveats" — caveats become a `REVISE`
or `REPLAN`, the caveats become the next manifest or plan. Anything
unrecognized re-prints the gate prompt with a no-parse note.

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator
    participant A as Agent (subagent)
    participant FS as .agent-runs/&lt;run-id&gt;/

    Note over U,FS: GATE 1 — manifest
    U->>O: /run "my-task description"
    O->>FS: write manifest.yaml skeleton
    O-->>U: drafter walks project; shows drafted manifest in chat
    U->>FS: edit manifest.yaml
    O->>U: chat prompt: APPROVE / REVISE / VIEW (v2.2.1+ chat keyword grammar)
    U->>O: APPROVE  (first non-whitespace token of next chat message, case-insensitive)
    O->>FS: append run.log: manifest COMPLETE

    Note over U,FS: GATE 2 — plan
    O->>A: spawn researcher subagent
    A->>FS: write research.md
    O->>A: spawn planner subagent
    A->>FS: write plan.md
    O->>U: chat prompt: APPROVE / REPLAN / BLOCK / VIEW
    U->>O: APPROVE
    O->>FS: append run.log: plan COMPLETE

    Note over U,FS: AGENT STAGES (no gate)
    O->>A: test-writer
    A->>FS: failing-tests-report.md
    O->>A: executor
    A->>FS: implementation-report.md
    O->>O: bash policy run
    O->>FS: policy-report.md
    O->>A: verifier
    A->>FS: verifier-report.md
    O->>A: manager
    A->>FS: manager-decision.md

    Note over U,FS: GATE 3 — manager-decision
    O->>U: chat prompt: APPROVE / BLOCK / REPLAN / VIEW
    U->>O: APPROVE
    O->>FS: append run.log: manager COMPLETE
    O-->>U: Pipeline complete
```

If the user types one of the recognized keywords other than `APPROVE`
(`REVISE` / `REPLAN` / `BLOCK`), the orchestrator routes accordingly:
`REVISE` re-spawns the drafter, `REPLAN` re-spawns the planner,
`BLOCK` writes `BLOCKED` to `run.log` and stops. Anything unrecognized
re-prints the gate prompt with a no-parse note (no guessing). Re-invoking
the same `/run resume <run-id>` later resumes from the next non-`COMPLETE`
stage. The log is the resume key.

---

## 5. What an agent stage actually sees

When the orchestrator spawns a subagent, it builds a prompt with three
pieces:

1. **Role file** (`.pipelines/roles/<role>.md`) verbatim — the contract
   for what this role does and what it never does.
2. **Run context** — the manifest plus every prior artifact, joined with
   `--- <filename> ---` separators.
3. **Run instructions** — the run id, the working directory, and the
   expected output filename.

```mermaid
flowchart TB
    subgraph Prompt["Prompt sent to fresh subagent"]
        Role["1. Role file content<br/>(verbatim)"]
        Sep1["---"]
        RC["2. RUN CONTEXT:<br/>--- manifest.yaml ---<br/>(content)<br/>--- research.md ---<br/>(content)<br/>--- plan.md ---<br/>(content)<br/>..."]
        Sep2["---"]
        Inst["3. RUN ID: 2026-05-09-my-task<br/>WORKING DIR: .agent-runs/.../<br/>Write your output to<br/>.agent-runs/.../&lt;artifact&gt;<br/>and stop."]
    end

    subgraph Agent["Subagent (general-purpose, fresh context)"]
        Read["Read inputs<br/>(no prior session)"]
        Work["Do the role's work<br/>(role file forbids overreach)"]
        Write["Write artifact<br/>to expected path"]
        Stop["Exit"]
    end

    Prompt --> Agent
    Read --> Work --> Write --> Stop
```

The orchestrator does **not** share its conversation history with the
subagent. The subagent sees the prompt and the filesystem. That is by
design: each stage starts with a clean head and only the artifacts on
disk.

---

## 6. The policy stage

The policy stage is the only non-agent automation in the pipeline. It
runs `python scripts/policy/run_all.py --run <run-id>`, which executes
each check in `CHECKS` and aggregates results. Exit code 0 means
PROMOTE-eligible; non-zero halts the run.

```mermaid
flowchart TB
    Start([orchestrator runs<br/>scripts/policy/run_all.py --run X]) --> Loop{For each check<br/>in CHECKS}
    Loop --> AP[check_allowed_paths.py<br/>--manifest .agent-runs/X/manifest.yaml]
    AP --> AP_OK{exit 0?}
    AP_OK -- yes --> NT[check_no_todos.py<br/>--exclude-paths foo,bar]
    AP_OK -- no --> Fail([fail with stdout])

    NT --> NT_OK{exit 0?}
    NT_OK -- yes --> AG[check_adr_gate.py<br/>--manifest .agent-runs/X/manifest.yaml]
    NT_OK -- no --> Fail

    AG --> AG_OK{exit 0?}
    AG_OK -- yes --> More{More checks?}
    AG_OK -- no --> Fail

    More -- yes --> Loop
    More -- no --> Pass([all pass — exit 0])

    classDef pass fill:#b3f0b3,stroke:#009900,color:#000
    classDef fail fill:#ffb3b3,stroke:#cc0000,color:#000
    class Pass pass
    class Fail fail
```

The default check set is generic. To add project-specific checks,
edit `scripts/policy/run_all.py` and append entries to `CHECKS`. Each
check is a list of CLI args; `run_all.py` runs them, captures stdout +
stderr, and reports any non-zero exit.

Examples of project-specific checks worth adding:

- `check_no_secrets.py` — scan diff for credential patterns
- `check_ffmpeg_wrapper.py` — enforce that all ffmpeg calls go through
  the wrapper module
- `check_no_console_log.py` — block `console.log` in production source

---

## 7. The judge layer (v0.4) — real-time action supervision

The judge layer is **opt-in supervision inside the executor stage**. It is **not a new pipeline stage**. When `.pipelines/action-classification.yaml` exists in your project, the orchestrator runs the executor stage as a **propose-execute loop** (the judged-executor handler in `run.md` Step 7a) instead of the standard single-spawn handler.

The interception happens at the **orchestrator's altitude, across spawns** — not per tool call. The platform does not let the orchestrator intercept the individual tool calls of a running subagent: a subagent runs autonomously to completion and cannot spawn the judge itself. So the executor performs reversible local work directly but **stops and writes an action proposal** (`pending-action.yaml`) for any external-facing or high-risk action and returns; the orchestrator then classifies the proposal, spawns the judge with isolated context, parses the verdict, and executes, revises, blocks, or escalates. The executor's role file carries the matching stop-and-propose protocol (gated on the same file's existence), and a non-bypassable PreToolUse hook hard-denies a direct external-facing or release-class attempt with `JUDGE_REVIEW_REQUIRED` so the protocol cannot be skipped.

What "non-bypassable" means precisely: a *direct* attempt — the executor simply calling the tool — is hard-denied, so the protocol cannot be skipped by accident or by the executor drifting into a confidently-wrong action, which is the failure mode the judge exists to catch. The one-shot approval sidecar (`judge-approved-next.txt`) is an **orchestrator-trust mechanism, not an adversarial sandbox**: the hook sees tool events, not which agent emitted them, so it cannot distinguish the orchestrator writing the sidecar from the executor writing it. The design trusts the executor not to forge the sidecar (the executor's role file never mentions it), and does not assume an executor deliberately engineering a bypass. The absolute destructive/secret deny floor is the part that holds regardless — it precedes the judge branch and is never reopened by the sidecar.

### Why this is here

Prompts don't hold across long context windows. The most expensive class of agent failure is not "the agent did nothing" but "the agent did the wrong thing because it confidently inferred authorization from inference instead of evidence." The classic example is the Lindy case (May 2026, Nate Jones): an agent sent 14 unauthorized emails because the operator's prior approvals trained the manifest-vs-action gap shut. Manual confirmation prompts don't help — operators learn to click "okay" reflexively.

The architectural fix is a second agent (the judge) whose sole loyalty is the manifest, evaluated in **context isolation** from the executor's reasoning chain. The judge cannot be persuaded by the executor's logic because it never sees it. It receives only the manifest, the action policy, prior judge decisions, and the structured action proposal.

### The classify → judge → execute loop

This loop runs at the orchestrator's altitude. The executor returns an action proposal (it does not call the judge or the action itself); the orchestrator classifies, judges, and on `revise` re-spawns the executor with the revision instruction.

```mermaid
flowchart TB
    Start([Executor returns an action proposal]) --> Classify[Classify against<br/>action-classification.yaml]
    Classify --> Route{Action class?}

    Route -- read_only --> AutoLog1[Execute action<br/>+ log auto_allow]
    Route -- reversible_write --> AutoLog2[Execute action<br/>+ log auto_allow]
    Route -- external_facing --> SpawnJudge[Spawn judge subagent<br/>with isolated context]
    Route -- high_risk --> SpawnJudge

    SpawnJudge --> Verdict{Judge verdict}

    Verdict -- allow + external_facing --> Execute[Execute action<br/>+ log judged_allow]
    Verdict -- allow + high_risk --> HumanConfirm[AskUserQuestion:<br/>APPROVE high-risk?]
    Verdict -- block --> Halt([Halt pipeline<br/>+ log judged_block])
    Verdict -- revise --> Revise[Send revision_instruction<br/>back to executor<br/>+ log judged_revise]
    Verdict -- escalate --> HumanEscalate[AskUserQuestion:<br/>escalation_question]

    HumanConfirm -- APPROVE --> ExecConfirmed[Execute action<br/>+ log human_confirmed]
    HumanConfirm -- Block --> HaltHuman([Halt pipeline<br/>+ log human_blocked])

    HumanEscalate -- APPROVE --> ExecEscalated[Execute action<br/>+ log human_confirmed]
    HumanEscalate -- Block --> HaltHuman

    Revise --> Retry[Re-spawn executor with<br/>revision_instruction;<br/>executor returns revised proposal]
    Retry --> Classify

    AutoLog1 --> Continue([Continue executor loop])
    AutoLog2 --> Continue
    Execute --> Continue
    ExecConfirmed --> Continue
    ExecEscalated --> Continue

    classDef agent fill:#cce5ff,stroke:#0066cc,color:#000
    classDef judge fill:#ccf2cc,stroke:#339933,color:#000
    classDef human fill:#ffd9b3,stroke:#cc6600,color:#000
    classDef stop fill:#ffb3b3,stroke:#cc0000,color:#000

    class Classify,Route agent
    class SpawnJudge,Verdict judge
    class HumanConfirm,HumanEscalate human
    class Halt,HaltHuman stop
```

The revise loop has a hard cap of 3 cycles per `action_id`. On the 4th cycle, the orchestrator auto-escalates with the question "Executor proposed this action 4 times after revise verdicts; revision loop is not converging." This prevents the executor from looping indefinitely against a misaligned manifest.

### The four risk classes

Defined in `.pipelines/action-classification.yaml`. Rules are evaluated top-to-bottom within each class; first match wins.

| Class | Examples | Routing |
| :--- | :--- | :--- |
| `read_only` | `cat`, `grep`, `ls`, `git log`, `pytest`, `ruff check` | Execute immediately + log |
| `reversible_write` | `str_replace_editor`, `git commit`, `git add`, `cp`, `mv`, `pip install` | Execute immediately + log |
| `external_facing` | `git push` (non-main, non-force), `gh pr create`, `curl -X POST`, `docker push`, `kubectl apply` | Judge required; ALLOW executes |
| `high_risk` | `rm -rf`, `git push --force`, `git push main`, `DROP TABLE`, `npm publish`, `chmod` | Judge required; ALLOW + human confirm |

Unmatched actions default to `reversible_write` — the safer assumption for any unclassified write-like action.

### Context isolation — what the judge sees and doesn't see

The judge is invoked as a fresh Claude subagent. The orchestrator deliberately supplies only the manifest, the matched action policy, prior judge decisions for this run (so the judge can detect re-proposals of blocked actions), and the structured action proposal block.

```mermaid
flowchart LR
    subgraph Executor["Executor subagent"]
        E_role["executor.md role file"]
        E_context["Full run context<br/>(manifest, plan,<br/>research, prior artifacts)"]
        E_reasoning["Internal reasoning chain<br/>(planning, retries,<br/>self-corrections)"]
        E_action["Proposed tool call"]
    end

    subgraph Judge["Judge subagent (fresh context)"]
        J_role["judge.md role file"]
        J_manifest["manifest.yaml"]
        J_policy["matched action policy<br/>(class + rule + note)"]
        J_prior["prior judge_decisions/<br/>this run"]
        J_proposal["action_proposal block"]
    end

    E_action -. orchestrator passes .-> J_proposal
    E_reasoning -. NOT shared .-> Judge
    E_context -. NOT shared .-> Judge

    classDef agent fill:#cce5ff,stroke:#0066cc,color:#000
    classDef judge fill:#ccf2cc,stroke:#339933,color:#000
    class Executor agent
    class Judge judge
```

This isolation is the mechanism. If the judge saw the executor's internal reasoning, it could be persuaded by the same chain of inferences that produced the over-broad action in the first place. Single-model-family correlated judgment defeats the purpose. Context isolation forces the judge to evaluate the action against the manifest only, using only what the executor cited as evidence.

The isolation is **load-bearing safety, not an artifact of a context-size limit**. Now that a 1M-token shared context makes passing the executor's full reasoning chain to the judge technically free, it is tempting to share it "so the judge has more to work with." The orchestrator must not. The judge's entire value is that it cannot be talked into the action by the reasoning that produced it; sharing that reasoning would silently delete the defense while leaving the machinery in place. The isolation is preserved deliberately even when sharing is free.

### Artifacts produced

Two new files land in the run directory when the judge layer is active:

- **`.agent-runs/<run-id>/judge-log.yaml`** — chronological log of every action: tool, arguments, matched class, disposition (auto_allow / judged_allow / judged_revise / judged_block / judged_escalate / human_confirmed / human_blocked), and the judge's reason and revision instruction when applicable. Both the verifier and the manager read this when reasoning about the executor's run.
- **`.agent-runs/<run-id>/judge-metrics.yaml`** — aggregate counts: `total_actions`, `by_class`, `by_disposition`, `escalation_rate`, `judge_invocations`, `revision_cycles`. The escalation rate is the operator's tuning signal — see USER-MANUAL.md.

A third internal directory holds per-action verdict files for replay and audit:

- **`.agent-runs/<run-id>/judge-decisions/<action_id>.yaml`** — one YAML file per judged action, written by the judge subagent. The orchestrator parses these directly; they are also read by the next judge invocation as `prior_judge_decisions`.

### When the judge is and isn't active

- **`.pipelines/action-classification.yaml` exists in the project** → the judged-executor propose-execute handler (`run.md` Step 7a) is used for the executor stage; the judge layer is active for that run.
- **`.pipelines/action-classification.yaml` does not exist** → the standard single-spawn executor handler is used exactly as in v0.3 and earlier; the judge layer is inactive. No `judge-log.yaml` or `judge-metrics.yaml` is produced, and the PreToolUse hook leaves external-facing actions at warn rather than redirecting them to the judge.

The decision is made once at the start of the run. Adding or removing the file mid-run does not retroactively change a stage that has already completed; a resumed run picks up the on-disk state at resume time.

### Relationship to other gates

The judge does **not** replace any existing gate. It supplements them at a different layer:

| Layer | Catches | When |
| :--- | :--- | :--- |
| Manifest gate | Wrong scope | Before any stage runs |
| Plan gate | Wrong approach | Before any code is written |
| **Judge (v0.4)** | **Unauthorized actions** | **In real time, during executor** |
| Policy stage | Path violations, TODOs, ADR changes | After executor, before verifier |
| Verifier stage | Manifest exit criteria not met | After policy |
| Manager gate | Anything verifier marked NOT MET | Final gate before merge |

The judge catches what the others can't: real-time interception of irreversible or external actions before they execute. The policy and verifier stages run **after** the executor has already done its work; the judge runs **during** the executor's work, so it can stop the action before it lands.

---

## 8. Single-AI hardening (v0.5) — critic, drift-detector, auto-promote

The v0.5 release adds three new stages to the pipeline that compensate for dropping dual-AI cross-family verification. They run between `verify` and `manager`:

```
verify → drift-detect → critique → auto-promote → manager
```

Each is a structural substitute for a different aspect of the dual-AI handoff that v0.3 enables but does not enforce inside the pipeline.

### drift-detector

A read-only role that compares the manifest's contract (`goal`, `expected_outputs`, `definition_of_done`, `non_goals`) against the assembled final state of the run — durable docs included (`CHANGELOG.md`, `README.md`, `USER-MANUAL.md`, ADRs, any project HANDOFF). It catches the gap class neither the judge (per-action) nor the verifier (per-criterion) can see: documents that say one thing while code says another, top-level ledger totals that don't match row counts, version strings out of sync across `pyproject.toml` / `__init__.py` / `CHANGELOG.md`, status-word abuse, "Closed" without evidence.

The role emits a structured `**Drift: <total> total, <blocker> blocker**` count line that the `auto-promote` stage parses directly. Blocker drift forbids auto-promotion regardless of other conditions.

### critic

A hostile cold read of every artifact in the run, in a fresh context. The critic role file is deliberately adversarial: hard rules forbid encouragement, severity softening, "no findings" without per-lens evidence, and trusting the verifier or executor at face value. The critic walks six lenses — engineering, UX, tests, docs, QA, scope — and emits a `**Findings: <total> total, <blocker> blocker, <critical> critical, <major> major, <minor> minor**` count line that `auto-promote` parses.

The critic is the structural substitute for the v0.3 cross-agent auditor when running with a single AI. Same model family, fresh context, contrarian role contract.

### auto-promote

A `role: pipeline` stage that runs `scripts/auto_promote.py`. It reads the artifacts produced by verifier, critic, drift-detector, policy, judge (when active), and executor, then checks six conditions:

1. Verifier-clean: zero `NOT MET` and zero `PARTIAL` criteria.
2. Critic-clean: zero blocker findings and zero critical findings.
3. Drift-clean: zero blocker drift items.
4. Policy-passed: `POLICY: ALL CHECKS PASSED` in `policy-report.md`.
5. Judge-clean: zero `judged_block` and zero `human_blocked` dispositions (vacuous when the v0.4 judge layer is inactive).
6. Tests-passed: a recognizable `N passed[, 0 failed]` or `all tests passed` signal in `implementation-report.md` (vacuous when the manifest's `forbidden_paths` covers the test directory — tests were out of scope for the run, so no signal is expected).

When all six pass, the script writes a preset `manager-decision.md` with `**Decision: PROMOTE**` and a citation block naming the evidence for each condition. The manager stage detects the preset (per Handler 4 in `skills/run/references/run.md`) and short-circuits the human-approval gate, advancing the pipeline automatically.

When any condition fails, the script writes `auto-promote-report.md` naming which conditions failed and exits 1. The manager stage runs normally with the human-approval gate active.

### Pre-edit fact-forcing in executor

The executor role file now contains a "Pre-edit fact-forcing gate" section. Before the first edit/write to any file in the run, the executor must produce a structured fact block (importers/callers, public API affected, data schema touched, manifest goal quoted verbatim). The drift-detector and critic both verify the block exists for every touched file.

### Manifest schema validation

`scripts/check_manifest_schema.py` enforces structural minimums on the manifest: `goal` ≥ 30 chars, `definition_of_done` ≥ 80 chars, non-empty `expected_outputs` / `non_goals` / `rollback_plan`, broad `allowed_paths` requires non-empty `forbidden_paths`, forbidden status words banned. Runs both at Phase A2 (run-start) and in the policy stage.

### Honest limit — single-model-family correlated blind spots

Critic and verifier run in the same model family. If both share a wrong assumption that fits the manifest, both sign off, auto-promote fires green, and the work ships wrong. Dual-AI (v0.3 cross-family audit) is the only structural defense against this. The v0.5 release does not replace v0.3; it provides single-AI projects a credible alternative when a second model family is not available. Recommended mitigation: periodic sample audit by a different model family on a weekly cadence.

---

## 9. The run.log resume mechanism

The `run.log` is the source of truth for "what's done." It is
append-only. Each line is one stage outcome. The orchestrator parses it
to decide where to start.

```
2026-05-09T14:30:00Z | manifest | COMPLETE | human approved
2026-05-09T14:32:11Z | research | COMPLETE | research.md written
2026-05-09T14:35:42Z | plan | COMPLETE | plan.md written
2026-05-09T14:35:50Z | plan | COMPLETE | human approved
2026-05-09T14:42:01Z | test-write | COMPLETE | failing-tests-report.md written
2026-05-09T14:51:33Z | execute | FAILED | artifact not produced (or empty)
```

```mermaid
stateDiagram-v2
    [*] --> ReadLog
    ReadLog --> ParseStages: read .agent-runs/X/run.log
    ParseStages --> FindResume: collect COMPLETE stage names
    FindResume --> AllDone: all stages COMPLETE?
    FindResume --> RunStage: first non-COMPLETE stage
    AllDone --> WrapUp
    RunStage --> WriteOutcome: stage handler runs
    WriteOutcome --> CheckOutcome
    CheckOutcome --> NextStage: COMPLETE
    CheckOutcome --> Halt: BLOCKED or FAILED
    NextStage --> FindResume
    Halt --> [*]: tell user resume command
    WrapUp --> [*]
```

This means:

- A `BLOCKED` or `FAILED` line does **not** mark the stage as done.
  Re-running picks up at that stage.
- The user never edits `run.log`. If a stage's outcome is wrong, the fix
  is in the underlying artifact or manifest, not the log.
- Crash-safety: if the orchestrator dies mid-stage, the missing
  `COMPLETE` line means the next run starts at that stage cleanly.

---

## 10. File layout — every file explained

```
agent-pipeline-claude/                        # the plugin
├── .claude-plugin/
│   └── plugin.json                      # plugin metadata, version
├── README.md                            # quick-start
├── USER-MANUAL.md                       # operator-facing
├── ARCHITECTURE.md                      # this file
├── CHANGELOG.md                         # version history
├── LICENSE                              # Apache-2.0
├── docs/
│   └── index.html                       # GitHub Pages landing page
├── skills/
│   ├── run/
│   │   ├── SKILL.md                     # thin shim — frontmatter + tool mapping
│   │   └── references/
│   │       └── run.md                   # canonical procedure: drafts manifest + orchestrates
│   ├── pipeline-init/
│   │   ├── SKILL.md
│   │   └── references/
│   │       └── pipeline-init.md
│   └── audit-init/
│       ├── SKILL.md
│       └── references/
│           └── audit-init.md
├── pipelines/
│   ├── feature.yaml                     # 8-stage feature flow
│   ├── bugfix.yaml                      # 7-stage bugfix flow
│   ├── manifest-template.yaml           # blank skeleton
│   ├── action-classification.yaml       # v0.4 — opt-in judge layer rules
│   └── roles/
│       ├── manifest-drafter.md          # v1.0 — drafts manifest from project spec
│       ├── researcher.md                # surfaces director decisions
│       ├── planner.md                   # produces plan.md §1-7
│       ├── test-writer.md               # writes failing tests only
│       ├── executor.md                  # makes tests green; v0.5 pre-edit fact-forcing
│       ├── verifier.md                  # independent fresh-context check
│       ├── drift-detector.md            # v0.5 — manifest contract vs assembled state
│       ├── critic.md                    # v0.5 — adversarial cold read, six lenses
│       ├── manager.md                   # PROMOTE/BLOCK/REPLAN; auto-promote-aware
│       └── judge.md                     # v0.4 — per-action real-time verdict
└── scripts/
    ├── __init__.py
    ├── check_allowed_paths.py           # diff vs. manifest allowed_paths
    ├── check_no_todos.py                # scan for TODO/FIXME/HACK
    ├── check_adr_gate.py                # ADRs are append-only
    ├── check_skill_packaging.py         # v1.1 — every SKILL.md only references its own folder
    ├── auto_promote.py                  # six-condition machine-checkable promote
    └── run_all.py                       # check runner
```

After `/pipeline-init`, your project gets:

```
<your-project>/
├── CLAUDE.md                            # scaffolded if absent
├── .gitignore                           # adds .agent-runs/
├── .pipelines/                          # copy of plugin's pipelines/
│   ├── feature.yaml
│   ├── bugfix.yaml
│   ├── manifest-template.yaml
│   └── roles/...
├── scripts/policy/                      # copy of plugin's scripts/
│   ├── __init__.py
│   ├── check_allowed_paths.py
│   ├── check_no_todos.py
│   ├── check_adr_gate.py
│   └── run_all.py
└── .agent-runs/                         # gitignored, created on first /run
    └── <run-id>/
        ├── manifest.yaml
        ├── research.md
        ├── plan.md
        ├── failing-tests-report.md
        ├── implementation-report.md
        ├── policy-report.md
        ├── verifier-report.md
        ├── manager-decision.md
        ├── judge-log.yaml               # v0.4 — written when judge layer is active
        ├── judge-metrics.yaml           # v0.4 — written when judge layer is active
        ├── judge-decisions/             # v0.4 — one YAML per judged action
        │   └── exec-NNN.yaml
        └── run.log
```

---

## 11. Extension points

The plugin is designed for projects to extend, not fork. The places to
extend:

| Extension | Where | Constraint |
|---|---|---|
| New pipeline type | `.pipelines/<name>.yaml` | Must use existing role names or add a role file |
| New role | `.pipelines/roles/<name>.md` | Must produce exactly one named artifact and stop |
| New policy check | `scripts/policy/check_<name>.py` + entry in `CHECKS` | Exit 0 = pass, non-zero = fail; print to stdout |
| Project conventions | `CLAUDE.md` | Roles read this; the planner is required to honor it |
| Manifest fields | `.pipelines/manifest-template.yaml` | Add field + inline comment; downstream roles may reference it |
| Judge classification rules | `.pipelines/action-classification.yaml` | Add entries under the appropriate class; first-match-wins per class; file presence opts the run into the judge layer |

Anti-patterns to avoid:

- Editing the role files mid-run. The contract changes mid-flight and the
  manager's verdict becomes meaningless.
- Editing the manifest mid-run. The orchestrator treats the manifest as
  immutable; if it needs to change, the manager returns REPLAN and you
  re-issue `/agent-pipeline-claude:run`.
- Adding a stage that produces multiple artifacts. The pipeline's resume
  logic and the verifier's input both depend on one-artifact-per-stage.
- Removing the manifest, plan, or manager gates. The plugin is built
  around three explicit gates; removing one means the run can promote
  without human review at a critical moment.

---

## 12. Why these defaults

Several non-obvious defaults exist because of real failures from prior
projects.

| Default | Reason |
|---|---|
| Three human gates, not two or four | Fewer means autonomous-mode-by-stealth; more means humans rubber-stamp |
| Manager must cite verifier verbatim | Encouragement and summarization let bad runs PROMOTE |
| Policy checks exit non-zero halts pipeline | "It's just a warning" is how scope creep gets in |
| Halt applies to ALL repo state, including unrelated cleanup | Otherwise an agent merges in-flight work while a question is open |
| `run.log` is append-only | Editing the log to "fix" a stage hides the underlying bug |
| Subagents have fresh context | Otherwise the executor inherits the planner's blind spots |
| Manifest has `forbidden_paths` not just `allowed_paths` | Belt-and-suspenders for high-risk dirs (e.g., production configs) |
| `definition_of_done` is required | Without it, the verifier has no objective check |
| Director-decisions are surfaced by the researcher, not the planner | The planner picks; if the researcher picked, no human got to weigh in |
| Cleanroom CI is recommended, not enforced | Some projects don't have Docker available; recommend strongly, don't gate |

If you find yourself wanting to override one of these, that's a real
decision worth recording in your project's `docs/adr/` directory.

---

## 13. Sequence summary — what happens end-to-end

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant CC as Claude Code
    participant Plugin as agent-pipeline-claude plugin
    participant Proj as your project
    participant Runs as .agent-runs/&lt;run-id&gt;/

    U->>CC: /agent-pipeline-claude:pipeline-init
    CC->>Plugin: read skills/pipeline-init/SKILL.md + references/pipeline-init.md
    CC->>U: ask: PRD / repo / description?
    U->>CC: PRD path or repo URL or description
    CC->>Proj: scaffold .pipelines/, scripts/policy/, CLAUDE.md
    CC->>U: orientation summary; suggest /agent-pipeline-claude:run

    U->>CC: /agent-pipeline-claude:run "short description"
    CC->>Plugin: read skills/run/SKILL.md + references/run.md
    CC->>CC: spawn manifest-drafter subagent (pre-stage)
    CC->>Proj: drafter walks spec/release-plan/scope-lock/CLAUDE.md/...
    CC->>Runs: create dir; drafter writes manifest.yaml + draft-provenance.md
    CC->>U: chat-message: drafted manifest + one-line provenance summary + chat gate prompt
    U->>CC: APPROVE (or REVISE/VIEW; first non-whitespace token, case-insensitive)

    loop for each stage in pipeline YAML
        CC->>Runs: read run.log to find resume point
        alt human gate (plan or manager)
            CC->>U: chat-message: stage summary + open questions + chat gate prompt
            U->>CC: APPROVE / REPLAN <changes> / BLOCK / VIEW
        else policy stage
            CC->>Proj: bash scripts/policy/run_all.py
            note over CC,Runs: failure -> standard failure shape with remediation
        else agent stage
            CC->>Plugin: read role file
            CC->>CC: spawn subagent with role + context
            CC->>Runs: subagent writes artifact
        end
        CC->>Runs: append run.log line
    end
    CC->>U: pipeline complete; show manager decision (or auto-PROMOTED)
```

---

## 14. Glossary

- **Manifest** — the human-authored contract for a single run. Lists
  goal, allowed paths, forbidden paths, non-goals, expected outputs,
  required gates, risk, rollback plan, definition of done, director
  notes.
- **Stage** — one entry in the pipeline YAML. Has a `name`, a `role`, an
  `artifact`, and optionally a `gate` or a `command`.
- **Role** — the kind of work a stage does. Defined in
  `.pipelines/roles/<role>.md`.
- **Artifact** — the single named file a stage produces, written into
  `.agent-runs/<run-id>/`.
- **Gate** — a checkpoint where the pipeline halts. Either
  `human_approval` (operator replies one of `APPROVE` / `REVISE` /
  `REPLAN` / `BLOCK` / `VIEW` in chat; the orchestrator parses the
  first non-whitespace token of the next message, case-insensitive)
  or implicit (a failing policy check or empty artifact). v1.3.0 → v2.1.0
  routed gates through `AskUserQuestion` modals; v2.2.1 reverses to chat
  with a deterministic keyword grammar after the operator-UX failure
  (the modal overlay hid chat context at gate-decision time). The
  modal-budget hook denies every `AskUserQuestion` during an active
  non-drafting run.
- **Subagent** — a fresh Claude Code agent spawned by the orchestrator
  for a single stage. Has no memory of the orchestrator's session.
- **Run ID** — `YYYY-MM-DD-<slug>`, the directory name under
  `.agent-runs/`.
- **Director** — the human who approves the manifest, the plan, and the
  manager decision.
- **Director notes** — free-form section in the manifest where the
  director records goals, constraints, and prior decisions that the
  researcher must surface.
- **Cleanroom CI** — a Docker-based reproduction of the test environment
  with a fresh dependency set, used to catch "works on my machine"
  bugs that local pytest misses.
- **Judge** (v0.4) — a fresh-context subagent whose only job is to
  evaluate a single proposed executor action against the manifest and
  return one of four verdicts: `allow`, `block`, `revise`, or
  `escalate`. Context-isolated from the executor's reasoning chain by
  design.
- **Action class** (v0.4) — the risk category assigned to each executor
  tool call by `.pipelines/action-classification.yaml`. One of
  `read_only`, `reversible_write`, `external_facing`, or `high_risk`.
  Determines whether the action is auto-executed, judged, or
  judged-plus-human-confirmed.
- **Escalation rate** (v0.4) — the fraction of executor actions that
  reach a human via the judge layer. The operator's tuning signal:
  too low means the classification rules are too permissive; too high
  means the rules are too tight and trust is being eroded by the
  cookie-banner effect.
