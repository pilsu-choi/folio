"""Domain mapper: adds contract fields to the common chunk record."""

from __future__ import annotations

import re

_CONTRACT_NO = re.compile(r"(?:계약\s*번호|Contract\s*No\.?)\s*[:：]?\s*([A-Za-z0-9\-_/]+)")
_PARTY = re.compile(r"(?:갑|을|甲|乙)\s*[:：(]\s*([^\n,)]{2,40})")
_ARTICLE = re.compile(r"제\s*(\d+)\s*조")


def map_contract(doc, chunk: dict) -> dict:
    """`(doc, chunk) -> dict`. Never remove the required fields (doc_id, chunk_id, content, ...)."""
    out = dict(chunk)
    text = chunk.get("content", "")
    first_page_text = " ".join(b.text or "" for b in (doc.pages[0].blocks if doc.pages else []))

    m = _CONTRACT_NO.search(text) or _CONTRACT_NO.search(first_page_text)
    out["contract_no"] = m.group(1) if m else None

    m = _PARTY.search(text) or _PARTY.search(first_page_text)
    out["party"] = m.group(1).strip() if m else None

    article = None
    for part in reversed(chunk.get("section_path", [])):
        m = _ARTICLE.search(part)
        if m:
            article = int(m.group(1))
            break
    if article is None:
        m = _ARTICLE.search(text)
        article = int(m.group(1)) if m else None
    out["article"] = article
    return out
