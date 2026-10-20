"""Synthetic scans: render a digital page at 150-200 dpi and degrade (rotate +-1.5deg, blur, noise, gray, JPEG q60).
Truth = source page text layer (txt + {"lines":[...]}). Seeded, deterministic."""
import json, os, random, numpy as np, pymupdf
from PIL import Image, ImageFilter
ROOT = os.path.expanduser("~/folio-m1-bench"); S = f"{ROOT}/samples"
SRC = [("d04_fsc_regional", 1), ("d06_fsc_audit", 2), ("d07_fsc_token_rule", 3), ("d12_terms_deposit", 2), ("d13_terms_bankloan", 3),
       ("d15_terms_pilates", 2), ("d02_fsc_virtual_asset", 2), ("d05_fsc_innovfin", 3), ("d10_kostat_elderly", 6), ("d16_terms_gym", 1)]
def degrade(img, rng, level):
    img = img.convert("L"); ang = rng.uniform(-1.5, 1.5)
    img = img.rotate(ang, resample=Image.BICUBIC, expand=False, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.6 + 0.5 * level))
    a = np.asarray(img, dtype=np.float32)
    a = (a - 128) * rng.uniform(0.8, 0.95) + 128 + rng.uniform(-8, 8)          # contrast / brightness drift
    h, w = a.shape; a += np.linspace(0, rng.uniform(-18, 18), w)[None, :]     # uneven illumination
    a += np.random.default_rng(rng.randint(0, 9999)).normal(0, 6 + 6 * level, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), ang
def main():
    man = []; os.makedirs(f"{S}/scan", exist_ok=True)
    for k, (src, pg) in enumerate(SRC):
        rng = random.Random(100 + k); dpi = rng.choice([150, 170, 200]); level = (k % 3) / 2
        page = pymupdf.open(f"{S}/pdf/{src}.pdf")[pg - 1]
        pix = page.get_pixmap(dpi=dpi); img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        out, ang = degrade(img, rng, level); id_ = f"s{k+1:02d}_{src.split('_', 1)[1]}_p{pg}"
        out.save(f"{S}/scan/{id_}.jpg", quality=60)
        txt = page.get_text(); lines = [l.strip() for l in txt.splitlines() if l.strip()]
        open(f"{S}/truth/{id_}.page1.txt", "w").write(txt)
        json.dump({"lines": lines}, open(f"{S}/truth/{id_}.truth.json", "w"), ensure_ascii=False, indent=1)
        man.append(dict(id=id_, file=f"scan/{id_}.jpg", kind="scan", pages=1, source_url=f"derived from {src} page {pg}", license="same as source doc",
                        truth=[f"truth/{id_}.page1.txt", f"truth/{id_}.truth.json"],
                        notes=f"synthetic scan: {dpi}dpi, rot {ang:+.2f}deg, blur, noise, gray, JPEG q60"))
    json.dump(man, open(f"{S}/manifest.scan.json", "w"), ensure_ascii=False, indent=1); print(len(man), "scans")
if __name__ == "__main__": main()
