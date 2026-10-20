"""Chunk Record: chunk/map output and index input.

Records are plain dicts so mappers can add any domain key. Only the keys below are checked.
"""

from __future__ import annotations

import hashlib
from typing import Any, NotRequired, TypedDict

from folio.errors import ContractError


class ChunkRecord(TypedDict):
    doc_id: str
    chunk_id: str
    content: str
    page: int | None
    section_path: list[str]
    source_uri: str
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


def make_chunk_id(doc_id: str, index: int, content: str) -> str:
    """Deterministic id: same doc, position and text → same id."""
    digest = hashlib.sha256(f"{doc_id}\n{index}\n{content}".encode()).hexdigest()[:12]
    return f"{doc_id}-{index:05d}-{digest}"
