"""Complex-table samples with exact truth.
 - 6 synthetic Korean tables (spec below -> HTML -> WeasyPrint PDF/PNG)
 - 3 real tables from FTC hwpx 표준약관 (cell spans read from hwpx), re-rendered alone.
Truth format (matches folio bench harness): {"page":1,"rows","cols","cells":[[str]],"spans":[{"row","col","rowspan","colspan"}]}
Merged cell text sits at its top-left position; covered positions are "". """
import json, os, html, re, pymupdf
from weasyprint import HTML
ROOT = os.path.expanduser("~/folio-m1-bench"); S = f"{ROOT}/samples"
# spec: list of rows; each cell = text | (text, rowspan, colspan) ; covered positions are omitted (HTML style)
def dense(spec):
    """HTML-style spec -> (cells, spans, R, C)"""
    R = len(spec); occ = {}; spans = []; texts = {}; C = 0
    for r, row in enumerate(spec):
        c = 0
        for cell in row:
            while (r, c) in occ: c += 1
            t, rs, cs = (cell, 1, 1) if isinstance(cell, str) else cell
            texts[(r, c)] = t
            for i in range(rs):
                for j in range(cs): occ[(r + i, c + j)] = 1
            if rs > 1 or cs > 1: spans.append(dict(row=r, col=c, rowspan=rs, colspan=cs))
            c += cs
        C = max(C, c)
    cells = [[texts.get((r, c), "") for c in range(C)] for r in range(R)]
    return cells, spans, R, C
def html_of(cells, spans, R, C, title=None):
    sp = {(s["row"], s["col"]): s for s in spans}; cov = set()
    for s in spans:
        for i in range(s["rowspan"]):
            for j in range(s["colspan"]):
                if i or j: cov.add((s["row"] + i, s["col"] + j))
    h = ["<table>"]
    for r in range(R):
        h.append("<tr>")
        for c in range(C):
            if (r, c) in cov: continue
            s = sp.get((r, c), {}); tag = "th" if r < 2 and C > 1 and len(spans) and False else "td"
            h.append(f'<td rowspan="{s.get("rowspan",1)}" colspan="{s.get("colspan",1)}">{html.escape(cells[r][c]).replace(chr(10), "<br>")}</td>')
        h.append("</tr>")
    return "".join(h) + "</table>"
CSS = "@page{size:A5 landscape;margin:12mm} body{font-family:'Noto Sans CJK KR';font-size:10pt} table{border-collapse:collapse;width:100%} td{border:1px solid #222;padding:4pt 6pt;text-align:center;vertical-align:middle}"
def render(id_, cells, spans, R, C, title=None):
    h = f"<html><head><meta charset=utf-8><style>{CSS}</style></head><body>{f'<p><b>{title}</b></p>' if title else ''}{html_of(cells, spans, R, C)}</body></html>"
    HTML(string=h).write_pdf(f"{S}/table/{id_}.pdf")
    pix = pymupdf.open(f"{S}/table/{id_}.pdf")[0].get_pixmap(dpi=200); pix.save(f"{S}/table/{id_}.png")
    json.dump(dict(page=1, rows=R, cols=C, cells=cells, spans=spans), open(f"{S}/truth/{id_}.table.json", "w"), ensure_ascii=False, indent=1)
M = lambda t, r=1, c=1: (t, r, c)
SPECS = {
 "t01_population_2level": ("지역별 인구 (가상 데이터)", [
   [M("지역", 2), M("인구(천 명)", 1, 2), M("증감", 1, 2)],
   ["2024년", "2025년", "증감(천 명)", "증감률(%)"],
   ["서울", "9,331", "9,296", "-35", "-0.4"], ["부산", "3,293", "3,270", "-23", "-0.7"], ["경기", "13,630", "13,712", "82", "0.6"]]),
 "t02_sales_3level": ("분기별 매출 (가상 데이터, 단위: 억 원)", [
   [M("부문", 3), M("2025년", 1, 4), M("합계", 3)],
   [M("상반기", 1, 2), M("하반기", 1, 2)],
   ["1분기", "2분기", "3분기", "4분기"],
   [M("국내", 2), "120", "135", "150", "162", "567"],
   ["해외", "80", "95", "110", "130", "415"],
   [M("합계", 1, 1), "200", "230", "260", "292", "982"]][:3] + [
   ["국내", "120", "135", "150", "162", "567"], ["해외", "80", "95", "110", "130", "415"], ["합계", "200", "230", "260", "292", "982"]]),
 "t03_budget_stub": ("사업별 예산 (가상 데이터, 단위: 백만 원)", [
   ["대분류", "세부 사업", "예산액", "집행액", "집행률"],
   [M("인프라", 3), "서버 증설", "5,000", "4,100", "82%"], ["네트워크", "2,000", "1,900", "95%"], ["보안", "1,500", "900", "60%"],
   [M("인력", 2), "신규 채용", "3,000", "2,700", "90%"], ["교육", "800", "720", "90%"],
   [M("총계", 1, 2), "12,300", "10,320", "84%"]]),
 "t04_application_form": ("교육 신청서 (가상 양식)", [
   ["성명", "홍길동", "생년월일", "1990. 01. 01."],
   ["연락처", M("010-0000-0000", 1, 3)],
   ["주소", M("서울특별시 종로구 세종대로 1", 1, 3)],
   [M("신청 과정", 2), M("데이터 분석 기초", 1, 3)], [M("2026. 11. 3. ~ 11. 28.", 1, 3)],
   ["비고", M("", 1, 3)]]),
 "t05_balance_sheet": ("요약 재무상태표 (가상 데이터, 단위: 억 원)", [
   [M("구분", 1, 2), "2024", "2025"],
   [M("자산", 3), "유동자산", "1,200", "1,350"], ["비유동자산", "3,400", "3,600"], ["자산총계", "4,600", "4,950"],
   [M("부채", 3), "유동부채", "900", "950"], ["비유동부채", "1,100", "1,050"], ["부채총계", "2,000", "2,000"],
   [M("자본", 1, 2), "2,600", "2,950"]]),
 "t06_grade_block": ("평가 기준표 (가상 데이터)", [
   [M("등급", 2), M("점수 구간", 1, 2), M("처우", 2)],
   ["하한", "상한"],
   ["S", "90", "100", M("특별 포상\n및 승진 우대", 2)], ["A", "80", "89"],
   ["B", "70", "79", M("정상", 2, 1)], ["C", "60", "69"],
   ["D", "0", "59", "재교육"]]),
}
if __name__ == "__main__":
    os.makedirs(f"{S}/table", exist_ok=True); man = []
    # t02 spec above is hand-fixed: rebuild cleanly
    SPECS["t02_sales_3level"] = (SPECS["t02_sales_3level"][0], [
        [M("부문", 3), M("2025년", 1, 4), M("합계", 3)],
        [M("상반기", 1, 2), M("하반기", 1, 2)],
        ["1분기", "2분기", "3분기", "4분기"],
        ["국내", "120", "135", "150", "162", "567"], ["해외", "80", "95", "110", "130", "415"], ["합계", "200", "230", "260", "292", "982"]])
    for id_, (title, spec) in SPECS.items():
        cells, spans, R, C = dense(spec); render(id_, cells, spans, R, C, title)
        man.append(dict(id=id_, file=f"table/{id_}.pdf", kind="table", pages=1, source_url="synthetic (make_tables.py)", license="own work, CC0",
                        truth=[f"truth/{id_}.table.json"], notes=f"synthetic HTML table {R}x{C}, {len(spans)} merged cells; also table/{id_}.png (200dpi)"))
    # real tables from hwpx
    k = 0
    for doc, label in [("wedding", "결혼준비대행업 표준계약서"), ("pilates", "요가·필라테스 표준약관"), ("gym", "체력단련장 표준약관")]:
        T = json.load(open(f"{ROOT}/stage/hwpx/{doc}.tables.json"))
        cand = [t for t in T if t["rows"] >= 3 and t["cols"] >= 3 and any(c["rowspan"] > 1 or c["colspan"] > 1 for c in t["cells"]) and sum(len(c["text"]) for c in t["cells"]) < 900]
        for t in cand[:2 if doc == "wedding" else 1]:
            k += 1; R, C = t["rows"], t["cols"]; cells = [[""] * C for _ in range(R)]; spans = []
            for c in t["cells"]:
                cells[c["row"]][c["col"]] = c["text"]
                if c["rowspan"] > 1 or c["colspan"] > 1: spans.append({x: c[x] for x in ("row", "col", "rowspan", "colspan")})
            id_ = f"t{6+k:02d}_real_{doc}"; render(id_, cells, spans, R, C, None)
            man.append(dict(id=id_, file=f"table/{id_}.pdf", kind="table", pages=1, source_url="https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=201",
                            license="공정거래위원회 표준약관(공공저작물)", truth=[f"truth/{id_}.table.json"], notes=f"{label}의 실제 표 {R}x{C}, 병합 {len(spans)}개; hwpx 셀 정보로 정답 생성, 표만 재렌더 (원본 레이아웃 아님)"))
    json.dump(man, open(f"{S}/manifest.table.json", "w"), ensure_ascii=False, indent=1); print(len(man), "tables")
