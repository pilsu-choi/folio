"""Merge partial manifests + figure entries -> samples/manifest.json and validate that every file/truth path exists."""
import json, os
S = os.path.expanduser("~/folio-m1-bench/samples")
FIG = {  # id -> (source doc, page)
 "f01_vasp_asset_count": ("d02_fsc_virtual_asset", 10), "f02_vasp_halt_pie": ("d02_fsc_virtual_asset", 5), "f03_vasp_solo_listing": ("d02_fsc_virtual_asset", 5),
 "f04_emp_rate_bar_line": ("d09_kostat_employment", 8), "f05_emp_infographic": ("d09_kostat_employment", 5), "f06_emp_sa_bar_line": ("d09_kostat_employment", 8),
 "f07_kgx_five_sectors": ("d01_fsc_kgx", 19), "f08_kgx_support_diagram": ("d01_fsc_kgx", 25), "f09_kgx_steel_roadmap": ("d01_fsc_kgx", 19), "f10_kgx_mobility_targets": ("d01_fsc_kgx", 25)}
man = []
for p in ["digital", "scan", "realscan", "table"]: man += json.load(open(f"{S}/manifest.{p}.json"))
dig = {m["id"]: m for m in man}
for id_, (doc, pg) in FIG.items():
    man.append(dict(id=id_, file=f"figure/{id_}.png", kind="figure", pages=1, source_url=dig[doc]["source_url"], license=dig[doc]["license"],
                    truth=[f"truth/{id_}.figure.json"], notes=f"crop of {doc} page {pg} at 150dpi; human-written Korean description + facts"))
bad = [x for m in man for x in [m["file"]] + m["truth"] if not os.path.exists(f"{S}/{x}")]
assert not bad, bad
json.dump(man, open(f"{S}/manifest.json", "w"), ensure_ascii=False, indent=1)
from collections import Counter
print(Counter(m["kind"] for m in man), "pages", sum(m["pages"] for m in man), "total", len(man))
