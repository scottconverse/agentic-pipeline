# SPDX-License-Identifier: Apache-2.0
"""Memory adapter interface plus Platform / OSS implementations.

Adapter is the only abstraction the rest of the package depends on.
Platform and OSS implementations lazy-import the mem0ai SDK so the
unit tests don't need it installed. A NullAdapter is used when Mem0
is disabled (`Mem0Config.enabled=False`) so callers never have to
branch on enable/disable - they always call adapter.add() etc.

Per PRD FR-8: agent must NOT call delete_*. Adapter enforces this at
the interface level - delete methods exist but raise on non-prune use.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    score: float | None = None
    metadata: dict[str, Any] | None = None


class MemoryAdapter(ABC):
    """Subset of Mem0's API surface needed by the pipeline."""

    @abstractmethod
    def add(self, messages: list[dict[str, str]] | str, *, metadata: dict[str, Any], filters: dict[str, str]) -> dict[str, Any]:
        ...

    @abstractmethod
    def search(self, query: str, *, filters: dict[str, str], top_k: int = 10) -> list[MemoryRecord]:
        ...

    @abstractmethod
    def get(self, memory_id: str) -> MemoryRecord | None:
        ...

    @abstractmethod
    def get_all(self, *, filters: dict[str, str]) -> list[MemoryRecord]:
        ...

    @abstractmethod
    def update(self, memory_id: str, content: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def list_entities(self) -> dict[str, Any]:
        ...

    # Delete operations are reserved for the prune command only. PolicyLayer
    # is the gatekeeper - it refuses to forward delete_* calls from the agent.
    def delete(self, memory_id: str, *, allowed_by_prune: bool = False) -> dict[str, Any]:  # pragma: no cover - abstract default
        if not allowed_by_prune:
            raise PermissionError("delete is only allowed via `pipeline mem0 prune`")
        return self._delete(memory_id)

    def delete_all(self, *, filters: dict[str, str], allowed_by_prune: bool = False) -> dict[str, Any]:  # pragma: no cover
        if not allowed_by_prune:
            raise PermissionError("delete_all is only allowed via `pipeline mem0 prune`")
        return self._delete_all(filters)

    def delete_entities(self, *, allowed_by_prune: bool = False) -> dict[str, Any]:  # pragma: no cover
        if not allowed_by_prune:
            raise PermissionError("delete_entities is only allowed via `pipeline mem0 prune`")
        return self._delete_entities()

    def _delete(self, memory_id: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _delete_all(self, filters: dict[str, str]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _delete_entities(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class NullAdapter(MemoryAdapter):
    """No-op adapter used when Mem0 is disabled. All calls succeed silently
    so callers never branch on enable/disable. Records nothing, returns
    empty results."""

    def add(self, messages, *, metadata, filters):
        return {"status": "disabled"}

    def search(self, query, *, filters, top_k=10):
        return []

    def get(self, memory_id):
        return None

    def get_all(self, *, filters):
        return []

    def update(self, memory_id, content):
        return {"status": "disabled"}

    def list_entities(self):
        return {"users": [], "agents": [], "apps": []}

    def _delete(self, memory_id):
        return {"status": "disabled"}

    def _delete_all(self, filters):
        return {"status": "disabled"}

    def _delete_entities(self):
        return {"status": "disabled"}


class PlatformAdapter(MemoryAdapter):
    """Mem0 Platform adapter. Wraps mem0ai.MemoryClient against api.mem0.ai.

    Lazy-imports mem0ai so the unit tests don't need the SDK on PATH.
    Raises ImportError on first method call when the SDK isn't installed.
    """

    def __init__(self, api_key: str, endpoint: str = "https://api.mem0.ai"):
        self._api_key = api_key
        self._endpoint = endpoint
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from mem0 import MemoryClient
            except ImportError as exc:  # pragma: no cover - tested via integration only
                raise ImportError(
                    "mem0ai SDK is required for Platform mode. "
                    "Install with `pip install mem0ai` and re-run."
                ) from exc
            self._client = MemoryClient(api_key=self._api_key, host=self._endpoint)
        return self._client

    def add(self, messages, *, metadata, filters):
        client = self._ensure_client()
        return client.add(messages, metadata=metadata, **filters)

    def search(self, query, *, filters, top_k=10):
        client = self._ensure_client()
        raw = client.search(query, top_k=top_k, **filters)
        return [
            MemoryRecord(
                id=str(item.get("id") or ""),
                content=str(item.get("memory") or item.get("content") or ""),
                score=float(item.get("score") or 0.0) if "score" in item else None,
                metadata=item.get("metadata"),
            )
            for item in (raw or [])
        ]

    def get(self, memory_id):
        client = self._ensure_client()
        item = client.get(memory_id)
        if not item:
            return None
        return MemoryRecord(
            id=str(item.get("id") or memory_id),
            content=str(item.get("memory") or item.get("content") or ""),
            metadata=item.get("metadata"),
        )

    def get_all(self, *, filters):
        client = self._ensure_client()
        raw = client.get_all(**filters)
        return [
            MemoryRecord(
                id=str(item.get("id") or ""),
                content=str(item.get("memory") or item.get("content") or ""),
                metadata=item.get("metadata"),
            )
            for item in (raw or [])
        ]

    def update(self, memory_id, content):
        client = self._ensure_client()
        return client.update(memory_id, data=content)

    def list_entities(self):
        client = self._ensure_client()
        if hasattr(client, "list_entities"):
            return client.list_entities()
        return {"users": [], "agents": [], "apps": []}

    def _delete(self, memory_id):
        return self._ensure_client().delete(memory_id)

    def _delete_all(self, filters):
        return self._ensure_client().delete_all(**filters)

    def _delete_entities(self):
        client = self._ensure_client()
        if hasattr(client, "delete_entities"):
            return client.delete_entities()
        return {"status": "no-op"}


class OssAdapter(MemoryAdapter):
    """Self-hosted OSS adapter. Wraps mem0ai.Memory against a local stack
    (Qdrant + Postgres started by `pipeline mem0 up`).

    Lazy-imports the SDK like PlatformAdapter.
    """

    def __init__(self, base_url: str = "http://localhost:8888"):
        self._base_url = base_url
        self._memory = None

    def _ensure_memory(self):
        if self._memory is None:
            try:
                from mem0 import Memory
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "mem0ai SDK is required for OSS mode. "
                    "Install with `pip install mem0ai` and re-run."
                ) from exc
            # The OSS Memory class is configured via env or kwargs depending
            # on SDK version; we pass base_url for newer versions and fall
            # back gracefully.
            try:
                self._memory = Memory(base_url=self._base_url)
            except TypeError:
                self._memory = Memory()
        return self._memory

    def add(self, messages, *, metadata, filters):
        return self._ensure_memory().add(messages, metadata=metadata, **filters)

    def search(self, query, *, filters, top_k=10):
        raw = self._ensure_memory().search(query, top_k=top_k, **filters)
        return [
            MemoryRecord(
                id=str(item.get("id") or ""),
                content=str(item.get("memory") or item.get("content") or ""),
                score=float(item.get("score") or 0.0) if "score" in item else None,
                metadata=item.get("metadata"),
            )
            for item in (raw or [])
        ]

    def get(self, memory_id):
        item = self._ensure_memory().get(memory_id)
        if not item:
            return None
        return MemoryRecord(
            id=str(item.get("id") or memory_id),
            content=str(item.get("memory") or item.get("content") or ""),
            metadata=item.get("metadata"),
        )

    def get_all(self, *, filters):
        raw = self._ensure_memory().get_all(**filters)
        return [
            MemoryRecord(
                id=str(item.get("id") or ""),
                content=str(item.get("memory") or item.get("content") or ""),
                metadata=item.get("metadata"),
            )
            for item in (raw or [])
        ]

    def update(self, memory_id, content):
        return self._ensure_memory().update(memory_id, data=content)

    def list_entities(self):
        mem = self._ensure_memory()
        if hasattr(mem, "list_entities"):
            return mem.list_entities()
        return {"users": [], "agents": [], "apps": []}

    def _delete(self, memory_id):
        return self._ensure_memory().delete(memory_id)

    def _delete_all(self, filters):
        return self._ensure_memory().delete_all(**filters)

    def _delete_entities(self):
        mem = self._ensure_memory()
        if hasattr(mem, "delete_entities"):
            return mem.delete_entities()
        return {"status": "no-op"}


def build_adapter(config) -> MemoryAdapter:
    """Factory: pick the right adapter based on Mem0Config.

    Returns NullAdapter when disabled. Defers Platform/OSS SDK import to
    first method call.
    """
    if not config.enabled:
        return NullAdapter()
    if config.mode == "platform":
        api_key = os.environ.get(config.platform.api_key_env, "")
        if not api_key:
            return NullAdapter()
        return PlatformAdapter(api_key=api_key, endpoint=config.platform.endpoint)
    if config.mode == "oss":
        return OssAdapter(base_url=config.oss.base_url)
    return NullAdapter()
