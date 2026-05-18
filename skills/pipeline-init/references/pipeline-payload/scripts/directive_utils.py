#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Directive-contract helpers for deterministic pipeline auto-approval.

Ported from agent-pipeline-codex v0.9.0 (scripts/directive_utils.py).
"""

from __future__ import annotations

import difflib
import hashlib
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DIRECTIVE_BOUND_RE = re.compile(r"\|\s*directive-bound\s*\|\s*COMPLETE\s*\|\s*hash=([a-f0-9]{64})\b")


class DirectiveError(ValueError):
    """Raised when a directive cannot be trusted for auto-approval."""


@dataclass(frozen=True)
class DirectiveContext:
    repo_root: Path
    run_id: str
    run_dir: Path
    directive_path: Path
    directive: dict[str, Any]
    current_hash: str
    bound_hash: str | None

    @property
    def author(self) -> str:
        author = self.directive.get("author") or {}
        if isinstance(author, dict):
            return str(author.get("name") or author.get("id") or "unknown")
        return str(author or "unknown")

    @property
    def authority(self) -> str:
        authority = self.directive.get("authority") or {}
        if isinstance(authority, dict):
            authority_type = str(authority.get("type") or "unknown")
            reference = str(authority.get("reference") or "unknown")
            return f"{authority_type}:{reference}"
        return str(authority or "unknown")


@dataclass(frozen=True)
class AssertionResult:
    id: str
    passed: bool
    evidence: str


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_yaml_file(path: Path) -> Any:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DirectiveError(f"{path.name} is malformed YAML: {exc}") from exc
    return loaded


def load_directive(repo_root: Path, run_id: str) -> DirectiveContext | None:
    run_dir = repo_root / ".agent-runs" / run_id
    directive_path = run_dir / "directive.yaml"
    if not directive_path.exists():
        return None
    loaded = load_yaml_file(directive_path)
    if not isinstance(loaded, dict):
        raise DirectiveError("directive.yaml must be a mapping")
    if loaded.get("version") != 1:
        raise DirectiveError("directive.yaml must declare `version: 1`")
    for field in ("author", "authority", "preapproved", "acceptance"):
        if field not in loaded:
            raise DirectiveError(f"directive.yaml missing required field `{field}`")
    current_hash = sha256_file(directive_path)
    return DirectiveContext(
        repo_root=repo_root,
        run_id=run_id,
        run_dir=run_dir,
        directive_path=directive_path,
        directive=loaded,
        current_hash=current_hash,
        bound_hash=read_bound_hash(run_dir / "run.log"),
    )


def read_bound_hash(run_log: Path) -> str | None:
    if not run_log.exists():
        return None
    for line in run_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = DIRECTIVE_BOUND_RE.search(line)
        if match:
            return match.group(1)
    return None


def ensure_hash_integrity(ctx: DirectiveContext) -> None:
    declared = ctx.directive.get("content_hash")
    if declared not in (None, "", ctx.current_hash):
        raise DirectiveError(
            f"directive content_hash mismatch: declared {declared}, actual {ctx.current_hash}"
        )
    if ctx.bound_hash is not None and ctx.bound_hash != ctx.current_hash:
        raise DirectiveError(
            f"directive hash changed since run start: bound {ctx.bound_hash}, current {ctx.current_hash}"
        )


def directive_bound_line(ctx: DirectiveContext, timestamp: str) -> str:
    return (
        f"{timestamp} | directive-bound | COMPLETE | hash={ctx.current_hash}; "
        f"author={ctx.author}; authority={ctx.authority}"
    )


def canonical_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=True, allow_unicode=False).splitlines(keepends=True)


def compare_preapproved(ctx: DirectiveContext, artifact: str, directive_key: str) -> tuple[bool, str]:
    preapproved = ctx.directive.get("preapproved")
    if not isinstance(preapproved, dict) or directive_key not in preapproved:
        raise DirectiveError(f"directive preapproved.{directive_key} is required")
    artifact_path = ctx.run_dir / artifact
    if not artifact_path.exists():
        raise DirectiveError(f"{artifact} missing")
    actual = load_yaml_file(artifact_path)
    expected = preapproved[directive_key]
    if actual == expected:
        return True, ""
    diff = "".join(
        difflib.unified_diff(
            canonical_yaml(expected),
            canonical_yaml(actual),
            fromfile=f"directive.preapproved.{directive_key}",
            tofile=artifact,
        )
    )
    return False, diff


def evaluate_assertions(
    *,
    ctx: DirectiveContext,
    assertions: list[dict[str, Any]],
    artifact_texts: dict[str, str],
    callable_namespace: str,
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for index, assertion in enumerate(assertions, start=1):
        if not isinstance(assertion, dict):
            results.append(AssertionResult(f"assertion-{index}", False, "assertion is not a mapping"))
            continue
        assertion_id = str(assertion.get("id") or f"assertion-{index}")
        kind = str(assertion.get("type") or "")
        try:
            if kind == "regex":
                results.append(_assert_regex(assertion_id, assertion, artifact_texts))
            elif kind == "contains":
                results.append(_assert_contains(assertion_id, assertion, artifact_texts))
            elif kind == "section":
                results.append(_assert_section(assertion_id, assertion, artifact_texts))
            elif kind == "artifact_exists":
                artifact = str(assertion.get("artifact") or "")
                path = ctx.run_dir / artifact
                passed = bool(artifact) and path.exists() and path.stat().st_size > 0
                evidence = f"{artifact} exists and is non-empty" if passed else f"{artifact} missing or empty"
                results.append(AssertionResult(assertion_id, passed, evidence))
            elif kind == "callable":
                results.append(_assert_callable(ctx, assertion_id, assertion, callable_namespace))
            else:
                results.append(AssertionResult(assertion_id, False, f"unsupported assertion type `{kind}`"))
        except Exception as exc:  # fail closed for malformed assertions/callables
            results.append(AssertionResult(assertion_id, False, f"{type(exc).__name__}: {exc}"))
    return results


def _artifact_text(assertion: dict[str, Any], artifact_texts: dict[str, str]) -> tuple[str, str]:
    artifact = str(assertion.get("artifact") or "plan.md")
    if artifact not in artifact_texts:
        raise DirectiveError(f"{artifact} is unavailable for assertion")
    return artifact, artifact_texts[artifact]


def _assert_regex(assertion_id: str, assertion: dict[str, Any], artifact_texts: dict[str, str]) -> AssertionResult:
    artifact, text = _artifact_text(assertion, artifact_texts)
    pattern = str(assertion.get("pattern") or "")
    flags = re.IGNORECASE if "i" in str(assertion.get("flags") or "") else 0
    count = len(re.findall(pattern, text, flags))
    minimum = int(assertion.get("min_count") or 1)
    passed = count >= minimum
    return AssertionResult(assertion_id, passed, f"{artifact}: regex `{pattern}` matched {count}/{minimum} required")


def _assert_contains(assertion_id: str, assertion: dict[str, Any], artifact_texts: dict[str, str]) -> AssertionResult:
    artifact, text = _artifact_text(assertion, artifact_texts)
    needle = str(assertion.get("text") or "")
    passed = bool(needle) and needle in text
    return AssertionResult(assertion_id, passed, f"{artifact}: contains `{needle}`" if passed else f"{artifact}: missing `{needle}`")


def _assert_section(assertion_id: str, assertion: dict[str, Any], artifact_texts: dict[str, str]) -> AssertionResult:
    artifact, text = _artifact_text(assertion, artifact_texts)
    heading = str(assertion.get("heading") or "")
    min_chars = int(assertion.get("min_chars") or 1)
    match = re.search(rf"^#+\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        return AssertionResult(assertion_id, False, f"{artifact}: section `{heading}` missing")
    next_heading = re.search(r"^#+\s+", text[match.end() :], re.MULTILINE)
    body = text[match.end() : match.end() + next_heading.start()] if next_heading else text[match.end() :]
    passed = len(body.strip()) >= min_chars
    return AssertionResult(assertion_id, passed, f"{artifact}: section `{heading}` has {len(body.strip())}/{min_chars} chars")


def _assert_callable(
    ctx: DirectiveContext,
    assertion_id: str,
    assertion: dict[str, Any],
    callable_namespace: str,
) -> AssertionResult:
    name = str(assertion.get("name") or "")
    if "." in name or name.startswith("_"):
        raise DirectiveError("registered callable names must be local public names")
    module = importlib.import_module(callable_namespace)
    func = getattr(module, name)
    passed, evidence = func(ctx, assertion.get("args") or {})
    return AssertionResult(assertion_id, bool(passed), str(evidence))
