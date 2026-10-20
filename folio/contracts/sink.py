"""Sink: index stage contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Sink(Protocol):
    def upsert(self, records: list[dict]) -> None:
        """Write records. Chunks of a doc_id not present in `records` are removed (replace semantics)."""

    def delete(self, doc_id: str) -> None: ...
