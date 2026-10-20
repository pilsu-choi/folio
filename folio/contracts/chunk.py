"""Chunk Record: chunk/map output and index input.

Records are plain dicts so mappers can add any domain key. Only the keys below are checked.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NotRequired, TypedDict

from folio.errors import ContractError


class ChunkRecord(TypedDict):
    doc_id: str
    chunk_id: str
    content: str
    page: int | None  # first page of the chunk
    section_path: list[str]
    source_uri: str
    pages: NotRequired[list[int]]  # full page range of a chunk spanning pages
    embedding: NotRequired[list[float]]
    chunk_type: NotRequired[str]
    bbox: NotRequired[list[float] | None]
    contextualized_content: NotRequired[str]


REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "doc_id": str,
    "chunk_id": str,
    "content": str,
    "page": (int, type(None)),
    "section_path": list,
    "source_uri": str,
}


def validate_chunk_record(record: Any, *, require_embedding: bool = False, where: str = "") -> None:
    """Raise ContractError naming every missing or mistyped required field."""
    prefix = f"{where}: " if where else ""
    if not isinstance(record, dict):
        raise ContractError(f"{prefix}chunk record must be a dict, got {type(record).__name__}")
    problems = []
    for name, typ in REQUIRED_FIELDS.items():
        if name not in record:
            problems.append(f"missing required field '{name}'")
        elif not isinstance(record[name], typ) or isinstance(record[name], bool):
            problems.append(f"field '{name}' has type {type(record[name]).__name__}")
    if require_embedding:
        emb = record.get("embedding")
        if not isinstance(emb, list) or not emb:
            problems.append("missing required field 'embedding'")
    if problems:
        chunk = record.get("chunk_id", "?")
        raise ContractError(f"{prefix}chunk {chunk}: " + "; ".join(problems))


def settings_hash(strategy: str, opts: dict[str, Any]) -> str:
    """Stable short hash of the chunk strategy and its parameters."""
    payload = json.dumps({"strategy": strategy, **opts}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def make_chunk_id(doc_id: str, block_range: str, settings: str) -> str:
    """Deterministic id from `doc_id` + the block range the chunk covers + strategy/parameter hash.

    `block_range` is produced by the chunker (e.g. ``"p1b0-p1b4"``, ``"p2b3#r10-19"`` for a split block);
    `settings` is :func:`settings_hash`. Same input and config → same id.
    """
    digest = hashlib.sha256(f"{doc_id}\n{block_range}\n{settings}".encode()).hexdigest()[:16]
    return f"{doc_id}-{digest}"
