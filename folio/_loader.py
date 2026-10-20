"""Resolve `module:attr` references used in pipeline YAML."""

from __future__ import annotations

import importlib
from typing import Any

from folio.errors import ConfigError


def load_callable(ref: str) -> Any:
    """Import `package.module:attr` (attr may be dotted) and return the object."""
    if not isinstance(ref, str) or ":" not in ref:
        raise ConfigError(f"expected 'module:function', got {ref!r}")
    module_name, _, attr = ref.partition(":")
    try:
        obj: Any = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import module {module_name!r} for {ref!r}: {exc}") from exc
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise ConfigError(f"{module_name!r} has no attribute {attr!r}") from exc
    return obj
