"""Generate parse fixtures with PyMuPDF only (built-in CJK font, no system fonts needed).

Run: python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
from PIL import Image

OUT = Path(__file__).parent
FONT = "korea"
W, H = 595, 842

DIGITAL_P1 = [
    ("h1", "1. 개요 Overview"),
    ("p", "이 문서는 folio 파서 시험용 디지털 PDF 입니다."),
    ("p", "The quick brown fox jumps over the lazy dog."),
    ("h2", "1.1 목록 List"),
    ("li", "- 첫 번째 항목 item one"),
    ("li", "- 두 번째 항목 item two"),
]
DIGITAL_P2 = [
    ("h1", "2. 결론 Conclusion"),
    ("p", "텍스트 레이어가 있는 페이지는 OCR 없이 처리됩니다."),
    ("p", "Second page paragraph with numbers 12345 and 6789."),
]
CONTRACT_P1 = [
    ("h1", "용역 계약서"),
    ("p", "갑 주식회사 가나다와 을 주식회사 라마바는 다음과 같이 계약을 체결한다."),
    ("h2", "제1조 (목적)"),
    ("p", "본 계약은 갑이 을에게 소프트웨어 개발 용역을 위탁하고 을이 이를 수행함에 필요한 사항을 정함을 목적으로 한다."),
    ("h2", "제2조 (계약기간)"),
    ("p", "계약기간은 2026년 1월 1일부터 2026년 12월 31일까지로 한다."),
]
CONTRACT_P2 = [
    ("h2", "제3조 (대금 및 지급)"),
    ("p", "총 계약금액은 일금 일억 원정(100,000,000원)으로 하며 부가가치세는 별도로 한다."),
    ("h2", "제4조 (비밀유지)"),
    ("p", "양 당사자는 계약 이행 중 알게 된 상대방의 비밀을 제3자에게 누설하여서는 아니 된다."),
]
SCAN_LINES = [
    ("h1", "스캔 문서 Scanned"),
    ("p", "이 페이지는 이미지로만 구성되어 있습니다."),
    ("p", "OCR is required for this page."),
]
TABLE_ROWS = [  # truth grid; None = covered by merge
    ["구분", "수량", None, "비고"],
    ["사과", "10", "20", "신선"],
    ["배", "5", "7", "냉장"],
]
TABLE_SPANS = [{"row": 0, "col": 1, "rowspan": 1, "colspan": 2}]


_FONT = pymupdf.Font(FONT)


def wrap(text: str, size: float, width: float) -> list[str]:
    out, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if cur and _FONT.text_length(trial, size) > width:
            out.append(cur)
            cur = word
        else:
            cur = trial
    return out + [cur]


def draw(page, lines, y=80, x=60):
    width = page.rect.width - 2 * x
    for kind, text in lines:
        size = {"h1": 20, "h2": 15}.get(kind, 11)
        for ln in wrap(text, size, width):
            page.insert_text((x, y), ln, fontname=FONT, fontsize=size)
            y += size + 3
        y += 12 if kind.startswith("h") else 8
    return y


def page_png(lines, dpi=144) -> bytes:
    d = pymupdf.open()
    draw(d.new_page(width=W, height=H), lines)
    return d[0].get_pixmap(dpi=dpi).tobytes("png")


def scanned_page(doc, lines, dpi=100):
    img = pymupdf.open()
    draw(img.new_page(width=W, height=H), lines)
    pix = img[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    p = doc.new_page(width=W, height=H)
    p.insert_image(p.rect, stream=pix.tobytes("jpg", jpg_quality=70))
    return p


def make_table(page, x0=60, y0=100, cw=(120, 90, 90, 120), rh=30):
    xs = [x0]
    for w in cw:
        xs.append(xs[-1] + w)
    ys = [y0 + rh * i for i in range(len(TABLE_ROWS) + 1)]
    shape = page.new_shape()
    for r in range(len(TABLE_ROWS)):
        for c in range(4):
            if TABLE_ROWS[r][c] is None:
                continue
            c2 = c + 2 if (r, c) == (0, 1) else c + 1
            shape.draw_rect(pymupdf.Rect(xs[c], ys[r], xs[c2], ys[r + 1]))
    shape.finish(width=1, color=(0, 0, 0))
    shape.commit()
    for r, row in enumerate(TABLE_ROWS):
        for c, t in enumerate(row):
            if t:
                page.insert_text((xs[c] + 6, ys[r] + 20), t, fontname=FONT, fontsize=11)


def main():
    # digital
    d = pymupdf.open()
    draw(d.new_page(width=W, height=H), DIGITAL_P1)
    draw(d.new_page(width=W, height=H), DIGITAL_P2)
    d.save(OUT / "digital.pdf", deflate=True)

    # contract
    d = pymupdf.open()
    draw(d.new_page(width=W, height=H), CONTRACT_P1)
    draw(d.new_page(width=W, height=H), CONTRACT_P2)
    d.save(OUT / "contract_ko.pdf", deflate=True)

    # table
    d = pymupdf.open()
    p = d.new_page(width=W, height=H)
    p.insert_text((60, 70), "표 시험 Table test", fontname=FONT, fontsize=15)
    make_table(p)
    d.save(OUT / "table.pdf", deflate=True)
    (OUT / "table.truth.json").write_text(
        json.dumps(
            {"page": 1, "rows": 3, "cols": 4, "cells": [[c or "" for c in r] for r in TABLE_ROWS], "spans": TABLE_SPANS},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )

    # scanned
    d = pymupdf.open()
    scanned_page(d, SCAN_LINES)
    d.save(OUT / "scanned.pdf", deflate=True)
    (OUT / "scanned.truth.json").write_text(
        json.dumps({"lines": [t for _, t in SCAN_LINES]}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # mixed: digital + scanned + broken font
    d = pymupdf.open()
    draw(d.new_page(width=W, height=H), DIGITAL_P2)
    scanned_page(d, SCAN_LINES)
    p = scanned_page(d, [("p", "깨진 폰트 페이지 broken font page")])
    for i in range(6):  # invisible OCR-like layer whose ToUnicode maps to private-use code points
        p.insert_text((60, 200 + i * 20), "Broken font text number %d lorem ipsum" % i, fontname="helv", render_mode=3)
    xref = p.get_fonts()[0][0]
    cmap = b"""/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Adobe-Identity-UCS def
/CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfrange <20> <7E> <E020> endbfrange
endcmap CMapName currentdict /CMap defineresource pop end end"""
    x = d.get_new_xref()
    d.update_object(x, "<<>>")
    d.update_stream(x, cmap)
    d.xref_set_key(xref, "ToUnicode", f"{x} 0 R")
    d.save(OUT / "mixed.pdf", deflate=True)

    # rotated + cropbox
    src = pymupdf.open()
    draw(src.new_page(width=H, height=W), DIGITAL_P2)  # landscape content
    d = pymupdf.open()
    p = d.new_page(width=W, height=H)
    p.show_pdf_page(p.rect, src, 0, rotate=90)  # vector text, drawn sideways on a portrait mediabox
    p.set_rotation(90)  # viewer shows it upright again (visible page is landscape)
    p = d.new_page(width=W, height=H)
    draw(p, DIGITAL_P2)
    p.set_cropbox(pymupdf.Rect(40, 50, 500, 600))
    d.save(OUT / "rotated.pdf", deflate=True)

    # corrupt
    data = (OUT / "digital.pdf").read_bytes()
    (OUT / "corrupt.pdf").write_bytes(data[:120])

    # images
    png = page_png(SCAN_LINES, dpi=100)
    im = Image.open(io.BytesIO(png)).convert("L")
    im.save(OUT / "image.png", dpi=(100, 100), optimize=True)
    im.convert("RGB").save(OUT / "image.jpg", quality=60, dpi=(100, 100))
    frames = [
        Image.open(io.BytesIO(page_png(l, dpi=72))).convert("L").convert("1") for l in (SCAN_LINES, DIGITAL_P2)
    ]
    frames[0].save(OUT / "multi.tiff", save_all=True, append_images=frames[1:], compression="tiff_lzw")


if __name__ == "__main__":
    main()
