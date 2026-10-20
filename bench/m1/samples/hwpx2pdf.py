"""Render FTC 표준약관 .hwpx (public, 공공누리) to PDF with a real text layer via HTML+WeasyPrint,
and dump each table as table-truth JSON (rowspan/colspan from the hwpx cellSpan).
usage: hwpx2pdf.py IN.hwpx OUT_PREFIX   -> OUT_PREFIX.pdf, OUT_PREFIX.tables.json"""
import sys, zipfile, json, html, re
import xml.etree.ElementTree as ET
from weasyprint import HTML
ln = lambda e: e.tag.rsplit("}", 1)[-1]
tables = []

def para_text(p):  # text of a paragraph, tables emitted inline
    out = []
    for run in p:
        if ln(run) != "run": continue
        for c in run:
            n = ln(c)
            if n == "t": out.append(html.escape("".join(c.itertext())))
            elif n == "tbl": out.append("</p>" + tbl_html(c) + "<p>")
    return "".join(out)

def cell_text(tc):
    ps = [para_text(p) for sl in tc if ln(sl) == "subList" for p in sl if ln(p) == "p"]
    return "<br>".join(x for x in ps if x)

def tbl_html(t):
    R, C = int(t.get("rowCnt")), int(t.get("colCnt")); cells = []; h = ["<table>"]
    for tr in t:
        if ln(tr) != "tr": continue
        h.append("<tr>")
        for tc in tr:
            if ln(tc) != "tc": continue
            addr = next(x for x in tc if ln(x) == "cellAddr"); sp = next(x for x in tc if ln(x) == "cellSpan")
            rs, cs = int(sp.get("rowSpan")), int(sp.get("colSpan")); ch = cell_text(tc)
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(ch.replace("<br>", "\n")))).strip()
            cells.append(dict(row=int(addr.get("rowAddr")), col=int(addr.get("colAddr")), rowspan=rs, colspan=cs, text=txt))
            h.append(f'<td rowspan="{rs}" colspan="{cs}">{ch}</td>')
        h.append("</tr>")
    h.append("</table>")
    tables.append(dict(rows=R, cols=C, cells=cells)); return "".join(h)

z = zipfile.ZipFile(sys.argv[1]); body = []
for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)):
    root = ET.fromstring(z.read(name))
    for p in root:
        if ln(p) == "p": body.append("<p>" + para_text(p) + "</p>")
css = """@page{size:A4;margin:18mm} body{font-family:'Noto Sans CJK KR';font-size:10pt;line-height:1.5}
p{margin:2pt 0} table{border-collapse:collapse;width:100%;margin:6pt 0;font-size:9pt} td{border:1px solid #333;padding:2pt 4pt;vertical-align:top}"""
HTML(string=f"<html><head><meta charset=utf-8><style>{css}</style></head><body>{''.join(body)}</body></html>").write_pdf(sys.argv[2] + ".pdf")
json.dump(tables, open(sys.argv[2] + ".tables.json", "w"), ensure_ascii=False)
print(sys.argv[2], "tables:", len(tables), "spanned:", sum(any(c["rowspan"] > 1 or c["colspan"] > 1 for c in t["cells"]) for t in tables))
