# SPDX-License-Identifier: Apache-2.0
"""Secret-redaction layer per PRD FR-11.

Before any add_memory call, candidates pass through `scrub()`. Matches
against the configured secret_patterns (regex) and block_paths
(literal path prefixes) drop the write entirely. The blocked content
goes to a local anti_pattern log entry for human review, never to Mem0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RedactionResult:
    allowed: bool
    matched_patterns: tuple[str, ...] = field(default_factory=tuple)
    matched_paths: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


# v3.0.1 (audit ENG-002 / QA-001): the prior GitHub clause required a HYPHEN
# (`gh[pousr]-`) where real GitHub tokens use an UNDERSCORE (`ghp_`, `gho_`,
# `github_pat_`), so it never matched a real PAT; Slack/GitLab/Google/JWT/
# URL-embedded/bare-credential formats were also uncovered and passed through
# scrub() into the Mem0 layer. Coverage is widened below; the fail-closed
# wiring (scrub() on add() and on every inbound search()) was already correct.
# Kept in lockstep with hooks/hook_utils.SECRET_PATTERNS (see
# tests/test_memory_layer.py::test_hook_and_memory_secret_patterns_lockstep).
_DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    # OpenAI / Mem0 prefixed keys (hyphen-delimited)
    r"\b(?:sk|m0)-[A-Za-z0-9_-]{20,}",
    # GitHub personal/OAuth/server/refresh/user tokens use an UNDERSCORE
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    # GitHub fine-grained PAT
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    # GitLab personal access token
    r"\bglpat-[A-Za-z0-9_-]{20,}\b",
    # Slack bot/user/app/refresh/legacy tokens
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    # Google API key
    r"\bAIza[0-9A-Za-z_-]{35}\b",
    # AWS access key id
    r"\bAKIA[0-9A-Z]{16}\b",
    # JSON Web Token (header.payload.signature)
    r"\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}",
    # Credentials embedded in a URL (scheme://user:pass@host). Bounded quantifiers
    # (audit QA-R-001): the prior unbounded `[..]*://[^..]+:[^..]+@` backtracked
    # quadratically — a 200KB benign string hung scrub() >15s. Bounds keep it
    # linear and portable (atomic groups would break on operator Python < 3.11).
    r"[A-Za-z][A-Za-z0-9+.\-]{0,30}://[^:@/\s]{1,128}:[^@/\s]{1,128}@",
    # PEM private key block
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    # Generic bearer-style token
    r"\bBearer\s+[A-Za-z0-9._-]{20,}",
    # Bare credential assignment (password=, secret:, api_key=, ...)
    r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*\S{6,}",
    # Stripe secret/restricted keys (underscore-delimited, unlike OpenAI's sk-)
    r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b",
    # npm access token
    r"\bnpm_[A-Za-z0-9]{20,}\b",
    # Google OAuth access token
    r"\bya29\.[0-9A-Za-z_-]{20,}",
)


_DEFAULT_BLOCK_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.config/gcloud",
    "~/.kube/config",
)


def _expand(path_template: str) -> str:
    return str(Path(path_template).expanduser()).replace("\\", "/").lower()


def scrub(
    text: str,
    secret_patterns: tuple[str, ...] | None = None,
    block_paths: tuple[str, ...] | None = None,
) -> RedactionResult:
    """Return RedactionResult(allowed=False, ...) if text contains anything
    matching a secret pattern or naming a blocked file path; otherwise
    RedactionResult(allowed=True).

    Fail-closed by design: if a pattern raises (malformed regex), that
    candidate is blocked - safer to lose a memory than leak a secret.
    """
    patterns = secret_patterns if secret_patterns is not None else _DEFAULT_SECRET_PATTERNS
    paths = block_paths if block_paths is not None else _DEFAULT_BLOCK_PATHS

    matched_patterns: list[str] = []
    for pat in patterns:
        try:
            if re.search(pat, text):
                matched_patterns.append(pat)
        except re.error:
            matched_patterns.append(f"<malformed:{pat}>")

    needle = text.replace("\\", "/").lower()
    matched_paths: list[str] = []
    for raw in paths:
        expanded = _expand(raw)
        # Match either the literal expanded form or the unexpanded form (e.g. "~/.ssh")
        if expanded and expanded in needle:
            matched_paths.append(raw)
        elif raw.lower() in needle:
            matched_paths.append(raw)

    if matched_patterns or matched_paths:
        reasons = []
        if matched_patterns:
            reasons.append(f"secret pattern(s): {len(matched_patterns)}")
        if matched_paths:
            reasons.append(f"blocked path(s): {', '.join(matched_paths)}")
        return RedactionResult(
            allowed=False,
            matched_patterns=tuple(matched_patterns),
            matched_paths=tuple(matched_paths),
            reason="; ".join(reasons),
        )
    return RedactionResult(allowed=True)
