# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures.

Determinism guard (audit QA-003): if ``CLAUDE_PROJECT_DIR`` is exported in the
runner's environment, every gate resolves ``.agent-runs`` from that tree and
the suite produces spurious failures (the QA pass saw 9 manifest-schema tests
go red for this reason). Clear it for the whole suite so results never depend
on the runner's ambient environment.
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_claude_project_dir(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
