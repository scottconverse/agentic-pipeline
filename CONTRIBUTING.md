# Contributing to agent-pipeline-claude

Thanks for considering a contribution. This plugin is small, opinionated, and built from real failure receipts — contributions are most welcome when they're grounded in the same kind of evidence.

## Quick links

- [README.md](README.md) — what the plugin does and how to install it.
- [USER-MANUAL.md](USER-MANUAL.md) — operator-facing reference.
- [ARCHITECTURE.md](ARCHITECTURE.md) — how the plugin is organized.
- [CHANGELOG.md](CHANGELOG.md) — version history.
- [Discussions](https://github.com/scottconverse/agent-pipeline-claude/discussions) — ideas, Q&A, show-and-tell.
- [Issues](https://github.com/scottconverse/agent-pipeline-claude/issues) — bugs and feature requests.

## Dev setup

This plugin is a collection of markdown role files + slash-command files + a small set of Python policy scripts. There's no build step; the "install" is the file-level install documented in the README.

To work on the plugin locally:

```bash
git clone https://github.com/scottconverse/agent-pipeline-claude.git
cd agent-pipeline-claude
# `main` is the current release line; branch from it for your change.
git checkout -b my-change main
```

If you want to point your installed plugin at the local clone instead of the marketplace version:

```bash
# Replace the marketplace dir with a symlink to your clone (POSIX):
rm -rf ~/.claude/plugins/marketplaces/agent-pipeline-claude
ln -s /path/to/your/agent-pipeline-claude ~/.claude/plugins/marketplaces/agent-pipeline-claude

# Or just edit ~/.claude/plugins/installed_plugins.json to point at your clone.
```

Restart your Cowork or CLI session to pick up the change.

## Running the tests

The plugin has a small test surface for the Python policy scripts:

```bash
cd agent-pipeline-claude
pip install pytest   # if not already installed
python -m pytest tests/ -v
```

Tests live at `tests/`. The role files (`pipelines/roles/*.md`) are spec-style documents — they aren't unit-testable, but the manifest-drafter has an integration test fixture you can run against (see `tests/README.md`).

## Lint / style

Python policy scripts follow stdlib-only style — no external dependencies. Use `ruff` if you want quick feedback locally; the repo doesn't enforce it in CI yet.

Markdown role files: keep them in second-person ("You receive ..."), bullets where natural, code-fenced examples where helpful. Each role file documents the role's input, output, and hard rules at the top.

## Contribution shapes

### Bug reports

Open an issue with:

- The slash command you ran and its full arguments.
- The expected outcome.
- The actual outcome (full chat-message transcript, with any error verbatim).
- Your Cowork or Claude Code CLI version.
- The plugin version — the `version` field in `.claude-plugin/plugin.json`, or run `claude plugin list`.
- The relevant `.agent-runs/<run-id>/run.log` if the bug surfaced during a run.

### Feature requests

Open a discussion in the [Ideas](https://github.com/scottconverse/agent-pipeline-claude/discussions/categories/ideas) board first. The plugin's surface is deliberately small; not every reasonable idea fits the design contract. A discussion gets you faster feedback than an issue + PR cycle.

### PRs

Small PRs welcome. Large PRs — open a discussion first. The plugin maintainer (Scott Converse) reviews PRs based on:

1. **Does it match the design contract?** v1.0 load-bearing decisions: Cowork-first, spec-aware drafting, one slash command, chat-native gates. PRs that re-introduce ceremony or duplicate the manifest-drafter's work get pushed back.
2. **Are the role files honest?** Every role file states: what you receive, what you produce, hard rules. PRs that introduce role files without that shape will be asked to refactor.
3. **Does it preserve the v0.5 hardening guarantees?** Critic, drift-detector, judge layer, auto-promote, strict schema validation. These are non-negotiable; PRs that weaken any of them won't merge.
4. **Tests for logic changes?** Per the project's coder-ui-qa-test standards, every change that modifies logic or a public interface includes or updates ≥1 automated test. Cosmetic changes (markdown copy, comments) exempt.
5. **Documentation in the same PR?** Per the project's documentation-sync rule, code changes that affect user-facing behavior, architecture, setup steps, configuration, public interfaces, or project positioning must include corresponding updates to README, USER-MANUAL, ARCHITECTURE, CHANGELOG, and the landing page where affected.

### Adding a new pipeline type

Drop a YAML at `pipelines/<name>.yaml` mirroring `feature.yaml`. Stages reference existing role files (`pipelines/roles/<role>.md`) or new ones you add in the same PR. Document the pipeline's intent + when to use it in either `docs/<name>-handbook.md` (if it's substantial enough to warrant one) or in `USER-MANUAL.md` § "Running a pipeline".

### Adding a new role

Drop a markdown file at `pipelines/roles/<role>.md`. The file is the role's complete spec — a fresh Claude session given just that file + the run context should be able to do the work. Use the existing role files as templates.

### Adding a new policy check

Add `scripts/check_<name>.py` (stdlib-only Python). Wire it into `scripts/run_all.py`. Add a `--version` flag for install-verification. Add a test at `tests/test_check_<name>.py` that covers at least one passing case and one failing case.

## Sign-off

All commits should include a DCO sign-off line (`git commit -s`). The maintainer enforces this on merge; PRs without it get force-rebased or rejected.

## License

Apache-2.0. By contributing, you agree your contribution is licensed under the same terms.

## Code of conduct

Be honest about failure modes. Be specific about what works. Don't oversell. The plugin exists because agent work fails in predictable ways — pretending the plugin solves problems it doesn't actually solve makes the failures worse, not better.
