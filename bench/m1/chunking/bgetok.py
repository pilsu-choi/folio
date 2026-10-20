"""Token counter for folio chunkers: BAAI/bge-m3 (XLM-R) tokenizer, no special tokens.
Use as tokenizer="bgetok:count" with PYTHONPATH containing this directory."""
from functools import lru_cache
from tokenizers import Tokenizer

_TOK = Tokenizer.from_file("/mnt/ssd/models/bge-m3/tokenizer.json")


def count(text: str) -> int:
    return len(_TOK.encode(text, add_special_tokens=False).ids)
