"""Stage contracts. Backward compatibility is guaranteed only for these four (SemVer major to change).

JSON Schemas live in `folio/contracts/schemas/` and are regenerated with
`python -m folio.contracts` (tests fail if they drift).
"""

from folio.contracts.chunk import (
    REQUIRED_FIELDS,
    ChunkRecord,
    make_chunk_id,
    settings_hash,
    validate_chunk_record,
)
from folio.contracts.config import PipelineConfig, RetrievalConfig, StepSpec, load_config, parse_config
from folio.contracts.document import (
    Block,
    BlockType,
    CellSpan,
    Document,
    Page,
    TextSource,
    content_hash,
    make_doc_id,
)
from folio.contracts.sink import Sink

__all__ = [
    "REQUIRED_FIELDS",
    "Block",
    "BlockType",
    "CellSpan",
    "ChunkRecord",
    "Document",
    "Page",
    "PipelineConfig",
    "RetrievalConfig",
    "Sink",
    "StepSpec",
    "TextSource",
    "content_hash",
    "load_config",
    "make_chunk_id",
    "make_doc_id",
    "parse_config",
    "settings_hash",
    "validate_chunk_record",
]
