"""Token counting and size-bounded text splitting shared by all chunk strategies."""

from __future__ import annotations

import math
import re
from collections.abc import Callable

from folio._loader import load_callable

Counter = Callable[[str], int]

# Hangul, CJK ideographs, kana: dense scripts. Everything else is split into words/numbers/punctuation.
_CJK = r"ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[^\W_{_CJK}]+|[^\s\w{_CJK}]|_")
_CJK_RUN = re.compile(rf"[{_CJK}]+")
_SENTENCE_RE = re.compile(r"\S.*?(?:[.!?。！？]+[\"')\]]*(?:\s+|$)|\n+|$)", re.DOTALL)


def count_tokens(text: str) -> int:
    """Model-free token estimate (no tokenizer download needed, deterministic).

    Latin words, numbers and each punctuation mark count as 1 token; a run of Hangul/CJK/kana of
    length L counts as ceil(L / 1.5), i.e. about 1 token per 1-2 characters. This is an approximation
    close to BPE counts for mixed Korean/English text; pass ``tokenizer="module:function"`` for exact counts.
    """
    total = 0
    for m in _TOKEN_RE.finditer(text):
        s = m.group()
        total += math.ceil(len(s) / 1.5) if _CJK_RUN.fullmatch(s) else 1
    return total


def resolve_counter(tokenizer: str | Counter | None) -> Counter:
    if tokenizer is None:
        return count_tokens
    return load_callable(tokenizer) if isinstance(tokenizer, str) else tokenizer


def _split_chars(text: str, max_tokens: int, count: Counter) -> list[str]:
    parts: list[str] = []
    rest = text
    while rest:
        if count(rest) <= max_tokens:
            parts.append(rest)
            break
        lo, hi = 1, len(rest)  # largest prefix with count <= max_tokens (at least 1 char)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count(rest[:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        parts.append(rest[:lo])
        rest = rest[lo:]
    return parts


def atomize(text: str, max_tokens: int, count: Counter) -> list[str]:
    """Split text into pieces each <= max_tokens: sentence -> word -> char boundaries. Concatenation == text."""
    atoms: list[str] = []
    last = 0
    pieces = []
    for m in _SENTENCE_RE.finditer(text):
        pieces.append(text[last : m.end()])  # keeps leading whitespace attached
        last = m.end()
    if last < len(text):
        pieces.append(text[last:])
    for sent in pieces:
        if count(sent) <= max_tokens:
            atoms.append(sent)
            continue
        for word in re.findall(r"\s*\S+\s*|\s+", sent):
            if count(word) <= max_tokens:
                atoms.append(word)
            else:
                atoms.extend(_split_chars(word, max_tokens, count))
    return atoms


def pack(atoms: list[tuple[str, object]], max_tokens: int, overlap: int, count: Counter) -> list[list[tuple[str, object]]]:
    """Greedy-pack (text, tag) atoms into windows with count(joined) <= max_tokens and token overlap."""
    sizes = [count(a[0]) for a in atoms]
    windows: list[list[tuple[str, object]]] = []
    i = 0
    n = len(atoms)
    while i < n:
        j, total = i, 0
        while j < n and total + sizes[j] <= max_tokens:
            total += sizes[j]
            j += 1
        if j == i:
            j = i + 1
        # custom counters may be non-additive: shrink until the joined window fits
        while j - i > 1 and count("".join(a[0] for a in atoms[i:j]).strip()) > max_tokens:
            j -= 1
        windows.append(atoms[i:j])
        if j >= n:
            break
        nxt, back = j, 0
        while overlap and nxt - 1 > i and back + sizes[nxt - 1] <= overlap:
            nxt -= 1
            back += sizes[nxt]
        i = nxt
    return windows
