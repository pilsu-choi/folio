"""Genuinely scanned public Korean pages from Wikimedia Commons / Wikisource (public domain per file page; 국가기록원 관보, 1948 자유신문).
No text truth (mark truth: []). Rendered at 150dpi to JPEG q85."""
import os, json, subprocess, pymupdf
ROOT = os.path.expanduser("~/folio-m1-bench"); S = f"{ROOT}/samples"
UA = "folio-bench/0.1 (research)"
SRC = [("Gwanbo_jeilho.pdf", "https://upload.wikimedia.org/wikipedia/commons/6/63/Gwanbo_jeilho.pdf", "https://commons.wikimedia.org/wiki/File:Gwanbo_jeilho.pdf", "대한민국 관보 제1호(1948.9.1), 국가기록원 소장, Commons 표기 Public domain (저작권법 제7조)", [1, 2]),
       ("jayu_sinmun_1948-07-13.pdf", "https://upload.wikimedia.org/wikipedia/commons/8/81/CNTS-00115191829_%E8%87%AA%E7%94%B1%E6%96%B0%E8%81%9E_1948-07-13.pdf", "https://ko.wikisource.org/wiki/파일:CNTS-00115191829_自由新聞_1948-07-13.pdf", "자유신문 1948-07-13, 국사편찬위원회 소장 스캔, Wikisource/Commons 표기 Public domain", [1, 2])]
os.makedirs(f"{S}/scan", exist_ok=True); man = []
for fn, url, page, lic, pages in SRC:
    path = f"{ROOT}/stage/real/{fn}"
    if not os.path.exists(path): subprocess.run(["curl", "-sL", "-A", UA, "-o", path, url], check=True)
    doc = pymupdf.open(path)
    for pg in pages:
        if pg > len(doc): continue
        id_ = f"r{len(man)+1:02d}_{fn.split('.')[0]}_p{pg}"
        doc[pg - 1].get_pixmap(dpi=150).pil_save(f"{S}/scan/{id_}.jpg", quality=85)
        man.append(dict(id=id_, file=f"scan/{id_}.jpg", kind="scan", pages=1, source_url=page, license=lic, truth=[],
                        notes="genuine scan, no text truth (OCR sanity/qualitative only); 1948 국한문혼용 세로/다단 레이아웃 가능, 현대 문서보다 어려움"))
json.dump(man, open(f"{S}/manifest.realscan.json", "w"), ensure_ascii=False, indent=1); print(len(man), "real scans", [len(pymupdf.open(f"{ROOT}/stage/real/{s[0]}")) for s in SRC])
