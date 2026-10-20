"""Map stage: chunk -> record. Default mapper keeps the chunk unchanged."""

from __future__ import annotations

from folio.contracts import Document


def common(doc: Document, chunk: dict) -> dict:
    return chunk
