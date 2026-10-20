"""Structure backends and the resolver used by `folio.parse` and the structure server."""

from __future__ import annotations

import os
from typing import Any

from folio._loader import load_callable
from folio.backends.base import PageStructure, Region, StructureBackend, Table, TableCell, Word

ALIASES = {
    "heuristic": "folio.backends.heuristic:HeuristicBackend",
    "http": "folio.backends.http:HttpBackend",
    "docling": "folio.backends.docling:DoclingBackend",
    "ppstructure": "folio.backends.ppstructure:PPStructureBackend",
}


def resolve_backend(spec: Any = "auto") -> StructureBackend:
    """spec: "auto" | alias | "module:Class" | {fn: "module:Class", **kwargs} | constructed backend object."""
    if spec is None or spec == "auto":
        spec = ALIASES["http" if os.environ.get("FOLIO_STRUCTURE_URL") else "heuristic"]
    if isinstance(spec, dict):
        kwargs = {k: v for k, v in spec.items() if k != "fn"}
        return _instantiate(spec["fn"], kwargs)
    if isinstance(spec, str):
        return _instantiate(spec, {})
    if isinstance(spec, type):
        return spec()
    return spec


def _instantiate(ref: str, kwargs: dict) -> StructureBackend:
    return load_callable(ALIASES.get(ref, ref))(**kwargs)


__all__ = ["PageStructure", "Region", "StructureBackend", "Table", "TableCell", "Word", "resolve_backend"]
