"""Figure crops (chart/diagram regions) from digital sample PDFs, rendered at 150dpi -> samples/figure/<id>.png.
Truth (description + facts) is human-written by looking at the crops: see figure_truth.py."""
import os, json, pymupdf
ROOT = os.path.expanduser("~/folio-m1-bench"); S = f"{ROOT}/samples"
# id, source doc, page, (x0,y0,x1,y1) as page fractions
CROPS = [
 ("f01_vasp_asset_count", "d02_fsc_virtual_asset", 10, (0.54, 0.07, 0.95, 0.35)),
 ("f02_vasp_halt_pie", "d02_fsc_virtual_asset", 5, (0.54, 0.50, 0.98, 0.77)),
 ("f03_vasp_solo_listing", "d02_fsc_virtual_asset", 5, (0.05, 0.47, 0.50, 0.66)),
 ("f04_emp_rate_bar_line", "d09_kostat_employment", 8, (0.0, 0.04, 1.0, 0.52)),
 ("f05_emp_infographic", "d09_kostat_employment", 5, (0.0, 0.03, 1.0, 0.97)),
 ("f06_emp_sa_bar_line", "d09_kostat_employment", 8, (0.0, 0.52, 1.0, 0.94)),
 ("f07_kgx_five_sectors", "d01_fsc_kgx", 19, (0.08, 0.268, 1.0, 0.425)),
 ("f08_kgx_support_diagram", "d01_fsc_kgx", 25, (0.15, 0.60, 0.88, 0.765)),
 ("f09_kgx_steel_roadmap", "d01_fsc_kgx", 19, (0.08, 0.69, 1.0, 0.955)),
 ("f10_kgx_mobility_targets", "d01_fsc_kgx", 25, (0.08, 0.14, 1.0, 0.25)),
]
if __name__ == "__main__":
    os.makedirs(f"{S}/figure", exist_ok=True)
    for id_, doc, pg, (a, b, c, d) in CROPS:
        p = pymupdf.open(f"{S}/pdf/{doc}.pdf")[pg - 1]; r = p.rect
        clip = pymupdf.Rect(r.x0 + a * r.width, r.y0 + b * r.height, r.x0 + c * r.width, r.y0 + d * r.height)
        p.get_pixmap(dpi=150, clip=clip).save(f"{S}/figure/{id_}.png")
    print(len(CROPS), "figures")
