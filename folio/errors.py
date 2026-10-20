"""Errors shared across stages."""


class FolioError(Exception):
    """Base class for folio errors."""


class ContractError(FolioError):
    """A record violates one of the stage contracts (Document JSON, Chunk Record, ...)."""


class ConfigError(FolioError):
    """Pipeline configuration is invalid or a `module:function` reference cannot be loaded."""


class DimensionMismatchError(FolioError):
    """Embedding dimension differs from the sink schema."""


class GenerationError(FolioError):
    """The generation chat step failed (LLM unreachable, bad response, ...)."""
