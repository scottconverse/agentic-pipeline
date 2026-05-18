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


_DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    r"(?:sk|m0|gh[pousr])-[A-Za-z0-9_-]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    # AWS access keys
    r"\bAKIA[0-9A-Z]{16}\b",
    # Generic bearer-style
    r"\bBearer\s+[A-Za-z0-9._-]{20,}",
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
