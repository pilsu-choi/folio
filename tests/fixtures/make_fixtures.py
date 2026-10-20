"""Generate parse fixtures with PyMuPDF only (built-in CJK font, no system fonts needed).

Run: python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import io
import json
import re
import zipfile
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


SLIDES = {
    "titles": ["folio 분기 보고", "주요 항목", "재고 표", "그림 슬라이드"],
    "bullets": ["- 첫째 항목", "- 둘째 항목", "  - 하위 항목 A", "  - 하위 항목 B", "- 셋째 항목"],
    "table": [["품목", "수량", ""], ["사과", "10", "20"], ["배", "5", "7"]],
    "table_spans": [{"row": 0, "col": 1, "rowspan": 1, "colspan": 2}],
    "notes": "발표자 노트: 분기 실적을 강조한다.",
    "picture_alt": "막대 차트 그림",
}
SHEETS = {  # visible sheets only; cell strings as parse should print them
    "매출": {"tables": [
        {"cells": [["2026 매출", "", ""]], "spans": [{"row": 0, "col": 0, "rowspan": 1, "colspan": 3}]},
        {"cells": [["제품", "수량", "금액"], ["사과", "10", "1000"], ["배", "5", "750.5"], ["합계", "15", "1750.5"]]},
    ]},
    "일정": {"tables": [{"cells": [["날짜", "달성률", "완료"], ["2026-01-05", "50%", "TRUE"],
                                  ["2026-02-10", "75%", "FALSE"]]}]},
}
MD = """# 제품 안내

소개 문단입니다. **굵게**와 `코드`가 있습니다.

## 설치

1. 패키지를 설치한다.
2. 서버를 실행한다.

- 항목 하나
  - 하위 항목 하나
  - 하위 항목 둘
- 항목 둘

### 옵션 표

| 이름 | 기본값 |
|------|--------|
| dpi | 144 |
| vlm | auto |

```python
print("hello")
```

![아키텍처 그림](https://example.com/arch.png)

## 마무리

> 인용문 문단입니다.
"""
MD_HEADINGS = [(1, "제품 안내"), (2, "설치"), (3, "옵션 표"), (2, "마무리")]
TXT_KO = "한글 인코딩 시험 문서입니다.\n\n두 번째 문단: 가나다라마바사 2026년 계약.\n세 번째 줄."


def bar_png(w=240, h=160) -> bytes:
    im = Image.new("RGB", (w, h), "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(im)
    for i, v in enumerate((40, 90, 65, 120)):
        d.rectangle((20 + i * 50, h - 10 - v, 55 + i * 50, h - 10), fill=(40 + i * 40, 90, 200 - i * 30))
    d.line((10, h - 10, w - 10, h - 10), fill="black")
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def make_pptx():
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.core_properties.created = prs.core_properties.modified = __import__("datetime").datetime(2026, 1, 1)
    t = SLIDES["titles"]
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = t[0]
    s.placeholders[1].text = "2026년 1분기"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = t[1]
    tf = s.placeholders[1].text_frame
    for i, line in enumerate(SLIDES["bullets"]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line.strip().removeprefix("- ")
        p.level = (len(line) - len(line.lstrip())) // 2
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = t[2]
    tbl = s.shapes.add_table(3, 3, Inches(1), Inches(2), Inches(6), Inches(1.8)).table
    for r, row in enumerate(SLIDES["table"]):
        for c, v in enumerate(row):
            tbl.cell(r, c).text = v
    tbl.cell(0, 1).merge(tbl.cell(0, 2))
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = t[3]
    pic = s.shapes.add_picture(io.BytesIO(bar_png()), Inches(2), Inches(2), Inches(4), Inches(2.7))
    pic._element.xpath(".//p:cNvPr")[0].set("descr", SLIDES["picture_alt"])
    s.notes_slide.notes_text_frame.text = SLIDES["notes"]
    prs.save(OUT / "slides.pptx")


def make_xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "매출"
    ws["A1"] = "2026 매출"
    ws.merge_cells("A1:C1")
    for r, row in enumerate([["제품", "수량", "금액"], ["사과", 10, 1000], ["배", 5, 750.5], ["합계", "=SUM(B4:B5)", "=SUM(C4:C5)"]], 3):
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
    ws2 = wb.create_sheet("일정")
    import datetime as dt

    ws2.append(["날짜", "달성률", "완료"])
    ws2.append([dt.date(2026, 1, 5), 0.5, True])
    ws2.append([dt.date(2026, 2, 10), 0.75, False])
    for r in (2, 3):
        ws2.cell(r, 1).number_format = "yyyy-mm-dd"
        ws2.cell(r, 2).number_format = "0%"
    wb.create_sheet("비밀").append(["숨김", "시트"])
    wb["비밀"].sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    # openpyxl cannot compute formulas: inject the cached values a spreadsheet app would have stored
    cached = {"B6": "15", "C6": "1750.5"}
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(OUT / "sheet.xlsx", "w", zipfile.ZIP_DEFLATED) as z:
        for info in sorted(src.namelist()):
            raw = src.read(info)
            if info == "xl/worksheets/sheet1.xml":
                txt = raw.decode()
                for ref, val in cached.items():
                    txt, n = re.subn(rf'(<c r="{ref}"[^>]*><f>[^<]*</f>)<v\s*/>|(<c r="{ref}"[^>]*><f>[^<]*</f>)<v></v>',
                                     lambda m: (m.group(1) or m.group(2)) + f"<v>{val}</v>", txt)
                    assert n == 1, ref
                raw = txt.encode()
            z.writestr(zipfile.ZipInfo(info, (2026, 1, 1, 0, 0, 0)), raw)


def make_figure_pdf():
    d = pymupdf.open()
    p = d.new_page(width=W, height=H)
    draw(p, [("h1", "3. 그림 Figure"), ("p", "아래 그림은 분기별 판매량을 나타냅니다. The chart below shows quarterly sales.")])
    p.insert_image(pymupdf.Rect(100, 220, 400, 420), stream=bar_png())
    p.insert_text((100, 440), "Figure 1. Quarterly sales", fontname=FONT, fontsize=10)
    d.save(OUT / "figure.pdf", deflate=True)


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
    im.convert("RGB").save(OUT / "image.bmp")
    im.convert("RGB").save(OUT / "image.webp", lossless=True)
    make_pptx()
    make_xlsx()
    make_figure_pdf()
    (OUT / "doc.md").write_text(MD, encoding="utf-8")
    (OUT / "legacy_cp949.txt").write_bytes(TXT_KO.encode("cp949"))
    (OUT / "formats.truth.json").write_text(json.dumps(
        {"slides": SLIDES, "sheets": SHEETS, "md_headings": MD_HEADINGS, "txt": TXT_KO}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    frames[0].save(OUT / "multi.tiff", save_all=True, append_images=frames[1:], compression="tiff_lzw")


if __name__ == "__main__":
    main()
