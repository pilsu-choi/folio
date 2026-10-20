"""Generation: ``folio.generation.answer(cfg, messages, ...)`` over the pipeline's search results.

The chat step is ``fn(messages, **opts) -> str`` (default ``folio.generation:http``, OpenAI-compatible).
"""

from folio.errors import GenerationError
from folio.generation.answer import Answer, answer, check_messages
from folio.generation.http import http

__all__ = ["Answer", "GenerationError", "answer", "check_messages", "http"]
