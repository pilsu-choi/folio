"""Chunk stage: Document -> Chunk Records.

    folio.chunk.adaptive(doc, **opts)   # default: structure tree, largest unit that fits max_tokens
    folio.chunk.page(doc, **opts)       # one chunk per page/slide/sheet, adaptive split on overflow
    folio.chunk.fixed(doc, **opts)      # structure-blind token windows (baseline)

Options: ``max_tokens=1024``, ``min_tokens=128``, ``tokenizer=None`` (None -> :func:`count_tokens`, or
``"module:function"`` text -> int), ``overlap=0`` (only ``fixed`` and forced fixed splits), ``include_header_footer=False``.
``max_tokens`` bounds ``contextualized_content``. Output is deterministic; every record is contract-validated.
"""

from folio.chunk.strategies import adaptive, fixed, page
from folio.chunk.tokens import count_tokens

__all__ = ["adaptive", "count_tokens", "fixed", "page"]
