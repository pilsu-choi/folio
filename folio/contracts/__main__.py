"""Write JSON Schemas for the contracts: `python -m folio.contracts [--check]`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

from folio.contracts.chunk import ChunkRecord
from folio.contracts.config import PipelineConfig
from folio.contracts.document import Document

SCHEMA_DIR = Path(__file__).parent / "schemas"


def schemas() -> dict[str, dict]:
    chunk = TypeAdapter(ChunkRecord).json_schema()
    chunk["additionalProperties"] = True  # mappers may add any key
    return {
        "document.schema.json": Document.model_json_schema(),
        "chunk_record.schema.json": chunk,
        "pipeline_config.schema.json": PipelineConfig.model_json_schema(),
    }


def main(argv: list[str]) -> int:
    check = "--check" in argv
    drift = []
    for name, schema in schemas().items():
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        path = SCHEMA_DIR / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                drift.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if drift:
        print("schemas out of date: " + ", ".join(drift) + " (run python -m folio.contracts)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
