"""Stage -> samples/pdf + text truth. Run on pgx from ~/folio-m1-bench with the venv.
Writes samples/pdf/<id>.pdf, samples/truth/<id>.page<N>.txt and returns manifest entries (merged by make_manifest.py)."""
import json, shutil, pymupdf, os
ROOT = os.path.expanduser("~/folio-m1-bench"); S = f"{ROOT}/samples"
FSC = "https://www.fsc.go.kr/no010101/"
KOS = "https://kostat.go.kr/board.es?mid=a10301010000&bid=210"
FTC = "https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=201"
FSC_LIC = "정부 보도자료(금융위원회). 공공누리(KOGL) 표시 확인 필요, 출처 표시 후 사용 전제 - 잠정"
KOS_LIC = "정부 보도자료(국가데이터처/통계청). 공공누리(KOGL) 표시 확인 필요, 출처 표시 후 사용 전제 - 잠정"
FTC_LIC = "공정거래위원회 표준약관(공공저작물). hwpx 원문을 hwpx2pdf.py로 PDF 재렌더(텍스트 레이어 정확), 레이아웃은 원본과 다름"
D = [  # id, staged file, source_url, license, notes
 ("d01_fsc_kgx", "fsc/fsc_87890_6.pdf", FSC+"87890", FSC_LIC, "61p 전략 보고서, 장/절 번호 헤딩, 도표 다수 (long)"),
 ("d02_fsc_virtual_asset", "fsc/fsc_87835_2.pdf", FSC+"87835", FSC_LIC, "11p 실태조사, 표+차트"),
 ("d03_fsc_phishing", "fsc/fsc_87882_2.pdf", FSC+"87882", FSC_LIC, "13p, 표/이미지, 일부 이미지 전용 페이지"),
 ("d04_fsc_regional", "fsc/fsc_87821_2.pdf", FSC+"87821", FSC_LIC, "10p 보도자료"),
 ("d05_fsc_innovfin", "fsc/fsc_87893_4.pdf", FSC+"87893", FSC_LIC, "10p 혁신금융서비스 지정 표"),
 ("d06_fsc_audit", "fsc/fsc_87894_2.pdf", FSC+"87894", FSC_LIC, "4p 감리조치 표 중심"),
 ("d07_fsc_token_rule", "fsc/fsc_87838_4.pdf", FSC+"87838", FSC_LIC, "3p 입법예고, 신구조문대비표"),
 ("d08_fsc_fund_status", "fsc/fsc_87900_2.pdf", FSC+"87900", FSC_LIC, "3p 판매현황 표"),
 ("d09_kostat_employment", "kostat/kostat_00.pdf", KOS, KOS_LIC, "81p 2026.9 고용동향 통합본, 번호 헤딩+차트 (long)"),
 ("d10_kostat_elderly", "kostat/kostat_14.pdf", KOS, KOS_LIC, "42p 고령층 부가조사 결과 (long)"),
 ("d11_kostat_tables", "kostat/kostat_02.pdf", KOS, KOS_LIC, "46p 통계표 부록, 숫자표 중심 (long)"),
 ("d12_terms_deposit", "hwpx/deposit.pdf", FTC, FTC_LIC, "예금거래기본약관(제10012호), 조/항 구조 계약"),
 ("d13_terms_bankloan", "hwpx/bankloan_corp.pdf", FTC, FTC_LIC, "은행여신거래기본약관(기업용) 조 구조 계약"),
 ("d14_form_wedding", "hwpx/wedding.pdf", FTC, FTC_LIC, "결혼준비대행업 표준계약서, 병합셀 양식 표 12개"),
 ("d15_terms_pilates", "hwpx/pilates.pdf", FTC, FTC_LIC, "요가·필라테스 표준약관, 표 8개"),
 ("d16_terms_gym", "hwpx/gym.pdf", FTC, FTC_LIC, "체력단련장 이용 표준약관, 표 3개"),
]
def pick_pages(doc, n=3):
    """text-rich pages spread across the document"""
    ok = [i for i, p in enumerate(doc) if len(p.get_text().strip()) > 300]
    if len(ok) <= n: return ok
    return [ok[round(k * (len(ok) - 1) / (n - 1))] for k in range(n)]
if __name__ == "__main__":
    man = []
    for id_, f, url, lic, notes in D:
        dst = f"{S}/pdf/{id_}.pdf"; shutil.copy(f"{ROOT}/stage/{f}", dst)
        doc = pymupdf.open(dst); tr = []
        for i in pick_pages(doc):
            t = f"truth/{id_}.page{i+1}.txt"; open(f"{S}/{t}", "w").write(doc[i].get_text()); tr.append(t)
        man.append(dict(id=id_, file=f"pdf/{id_}.pdf", kind="digital", pages=len(doc), source_url=url, license=lic, truth=tr, notes=notes))
    json.dump(man, open(f"{S}/manifest.digital.json", "w"), ensure_ascii=False, indent=1)
    print(len(man), "digital docs", sum(m["pages"] for m in man), "pages")
