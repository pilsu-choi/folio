"""Pipeline configuration (YAML) contract.

Each stage is either `"module:function"` or `{fn: "module:function", **opts}`.
`${VAR}` / `${VAR:-default}` in string values are expanded from the environment at load time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from folio.errors import ConfigError

DEFAULT_PARSE = "folio.parse:default"
DEFAULT_CHUNK = "folio.chunk:adaptive"
DEFAULT_EMBED = "folio.embed:http"


class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fn: str
    opts: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def coerce(cls, value: Any) -> StepSpec | None:
        if value is None or isinstance(value, StepSpec):
            return value
        if isinstance(value, str):
            return cls(fn=value)
        if isinstance(value, dict):
            data = dict(value)
            if "fn" not in data:
                raise ValueError("step mapping requires 'fn'")
            fn = data.pop("fn")
            return cls(fn=fn, opts=data)
        raise ValueError(f"step must be a string or mapping, got {type(value).__name__}")


class RetrievalConfig(BaseModel):
    """Search settings for the pipeline's index (``retrieval:`` in the YAML). Requests may override only
    ``k``, ``mode`` and ``filters``; field weights, fusion and rerank stay in config."""

    model_config = ConfigDict(extra="forbid")

    fn: str | None = Field(default=None, description="custom retriever factory(sink, embed, **settings)")
    mode: Literal["hybrid", "bm25", "vector"] = "hybrid"
    k: int = Field(default=10, ge=1)
    fields: dict[str, float] = Field(default_factory=lambda: {"content": 1.0}, description="BM25 fields -> weight")
    rrf_k: int = Field(default=60, ge=1)
    candidates: int | None = Field(default=None, ge=1, description="per-leg candidates; default max(k*3, 30)")
    rerank: StepSpec | None = None
    required_filters: list[str] = Field(default_factory=list)

    @field_validator("rerank", mode="before")
    @classmethod
    def _coerce_rerank(cls, value: Any) -> Any:
        return StepSpec.coerce(value)

    @field_validator("fields")
    @classmethod
    def _fields_nonempty(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("retrieval.fields must name at least one field")
        return value

    def settings(self) -> dict[str, Any]:
        """Constructor kwargs for a retriever (everything but ``fn``/``rerank``/``required_filters``)."""
        return self.model_dump(exclude={"fn", "rerank", "required_filters"})


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    parse: StepSpec = Field(default_factory=lambda: StepSpec(fn=DEFAULT_PARSE))
    chunk: StepSpec = Field(default_factory=lambda: StepSpec(fn=DEFAULT_CHUNK))
    map: StepSpec | None = None
    embed: StepSpec | None = Field(default_factory=lambda: StepSpec(fn=DEFAULT_EMBED))
    sink: dict[str, Any]
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    storage: str = ".folio/artifacts"
    config_dir: str | None = Field(default=None, description="dir holding schemas/ and mappers.py")

    @field_validator("parse", "chunk", "map", "embed", mode="before")
    @classmethod
    def _coerce_step(cls, value: Any) -> Any:
        return StepSpec.coerce(value)

    @field_validator("sink")
    @classmethod
    def _sink_has_type(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "type" not in value and "fn" not in value:
            raise ValueError("sink requires 'type' (opensearch|milvus|pgvector) or 'fn'")
        return value

    @property
    def index(self) -> str:
        return str(self.sink.get("index") or self.sink.get("collection") or self.sink.get("table") or self.name)


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name} is not set")

        return _ENV.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def parse_config(data: dict[str, Any], *, name: str | None = None, config_dir: str | None = None) -> PipelineConfig:
    data = expand_env(dict(data))
    if name and "name" not in data:
        data["name"] = name
    if config_dir and not data.get("config_dir"):
        data["config_dir"] = config_dir
    try:
        return PipelineConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid pipeline config {data.get('name', name)!r}: {exc}") from exc


def load_config(path: str | Path) -> PipelineConfig:
    """Load a pipeline YAML. `config_dir` defaults to the parent of `pipelines/` (project root)."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"pipeline config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    root = path.parent.parent if path.parent.name == "pipelines" else path.parent
    return parse_config(data, name=path.stem, config_dir=str(root.resolve()))
