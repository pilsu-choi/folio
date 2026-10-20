"""Chunk stage: Document -> Chunk Records.

    folio.chunk.auto(doc, **opts) -> list[dict]   # picks a strategy per document
    folio.chunk.section / table / page / fixed    # force one strategy

Options: ``max_tokens=512``, ``overlap_tokens=0``, ``min_tokens=0`` (merge smaller chunks into the previous
one when they fit), ``tokenizer="module:function"`` (text -> int; default :func:`count_tokens`),
``include_header_footer=False``, and for ``auto`` also ``strategy=`` to override the choice.
No chunk exceeds ``max_tokens`` under the same counter. Output is deterministic.
"""

from folio.chunk.strategies import auto, fixed, page, section, table
from folio.chunk.tokens import count_tokens

__all__ = ["auto", "count_tokens", "fixed", "page", "section", "table"]
