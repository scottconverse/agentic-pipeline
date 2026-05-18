# SPDX-License-Identifier: Apache-2.0
"""Identity derivation per PRD section 5.2.

Derives user_id (SHA-256 of git user.email, first 16 hex chars),
agent_id (fixed "claude-code"), app_id (slug of git remote origin),
and run_id (branch-sha-epoch). user_id is a hash so the user's email
never leaves the machine verbatim; the same user on a different
machine produces the same user_id (deterministic across machines).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IdentityContext:
    user_id: str
    agent_id: str
    app_id: str
    run_id: str
    branch: str
    repo_root: Path

    def as_filter(self) -> dict[str, str]:
        """Default search filter: user_id + app_id."""
        return {"user_id": self.user_id, "app_id": self.app_id}

    def as_write_keys(self, *, include_run: bool = True) -> dict[str, str]:
        """Default write keys: user_id + agent_id + app_id (+ run_id)."""
        keys = {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "app_id": self.app_id,
        }
        if include_run:
            keys["run_id"] = self.run_id
        return keys


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    lowered = value.lower().strip()
    slugged = _SLUG_RE.sub("-", lowered).strip("-")
    return slugged or "unknown"


def _git_config(repo_root: Path, key: str) -> str:
    proc = subprocess.run(
        ["git", "config", "--get", key],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _git_remote_url(repo_root: Path, remote: str = "origin") -> str:
    proc = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _git_branch(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def _git_short_sha(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "nogit"
    return proc.stdout.strip() or "nogit"


def _derive_user_id(repo_root: Path) -> str:
    """SHA-256(git user.email) first 16 hex chars. Email never leaves the box."""
    email = _git_config(repo_root, "user.email") or "anonymous"
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    return digest[:16]


def _derive_app_id(repo_root: Path) -> str:
    """slug(git remote get-url origin). Falls back to repo dir name."""
    remote_url = _git_remote_url(repo_root)
    if remote_url:
        candidate = remote_url
        for stripped in (".git",):
            if candidate.endswith(stripped):
                candidate = candidate[: -len(stripped)]
        slug = _slug(candidate.rstrip("/"))
        if slug != "unknown":
            return slug
    return _slug(repo_root.name)


def _derive_run_id(repo_root: Path) -> tuple[str, str]:
    """Returns (run_id, branch). run_id = `{branch}-{short-sha}-{epoch}`."""
    branch = _git_branch(repo_root)
    sha = _git_short_sha(repo_root)
    epoch = int(time.time())
    run_id = f"{branch}-{sha}-{epoch}"
    return run_id, branch


def derive_identity(repo_root: Path, agent_id: str = "claude-code") -> IdentityContext:
    """Apply the PRD section 5.2 derivation rules.

    Pure function; only depends on git output. Returns an IdentityContext
    with stable user_id, fixed agent_id="claude-code", repo-scoped app_id,
    and a time-scoped run_id (branch-sha-epoch).
    """
    user_id = _derive_user_id(repo_root)
    app_id = _derive_app_id(repo_root)
    run_id, branch = _derive_run_id(repo_root)
    return IdentityContext(
        user_id=user_id,
        agent_id=agent_id,
        app_id=app_id,
        run_id=run_id,
        branch=branch,
        repo_root=repo_root,
    )
