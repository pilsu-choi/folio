"""Compare converter outputs against reference PDFs exported by the authoring app (Hancom, Word).

usage: python evaluate.py BENCH_DIR [--ext hwpx] [--pdf-tools a,b] [--native-tools kordoc] [--out out]
PDF tools: <out>/<tool>/<name>.pdf ; native tools (kordoc): <out>/<tool>/<name>.md
"""
import argparse
import json
import re
import statistics
from pathlib import Path

import pymupdf
from rapidfuzz.distance import Indel

ap = argparse.ArgumentParser()
ap.add_argument("bench", type=Path)
ap.add_argument("--ext", default="hwpx")
ap.add_argument("--pdf-tools", default="libreoffice,rhwp,hwpforge")
ap.add_argument("--native-tools", default="kordoc")
ap.add_argument("--out", default="out")
ARGS = ap.parse_args()
BENCH, EXT, OUT = ARGS.bench, ARGS.ext, ARGS.bench / ARGS.out
PDF_TOOLS = [t for t in ARGS.pdf_tools.split(",") if t]
NATIVE_TOOLS = [t for t in ARGS.native_tools.split(",") if t]
WS = re.compile(r"\s+")
POS_TOL = 6.0  # pt


def norm(s: str) -> str:
    return WS.sub("", s)


def lcs_scores(ref: str, out: str) -> tuple[float, float]:
    """Character recall / precision based on LCS (Indel distance)."""
    if not ref or not out:
        return 0.0, 0.0
    lcs = (len(ref) + len(out) - Indel.distance(ref, out)) / 2
    return lcs / len(ref), lcs / len(out)


def page_words(page, ref_rect=None):
    """Word centers; scaled to ref page size so differently sized exports are comparable."""
    sx = ref_rect.width / page.rect.width if ref_rect else 1.0
    sy = ref_rect.height / page.rect.height if ref_rect else 1.0
    return [(w[4], (w[0] + w[2]) / 2 * sx, (w[1] + w[3]) / 2 * sy) for w in page.get_text("words")]


def bag_recall(ref: str, out: str) -> float:
    """Order-insensitive character-bigram recall."""
    from collections import Counter
    a, b = Counter(zip(ref, ref[1:])), Counter(zip(out, out[1:]))
    return sum((a & b).values()) / max(1, sum(a.values()))


def word_recall(ref: str, out: str) -> float:
    """Whitespace-token bag recall: drops when a converter loses or merges word spacing."""
    from collections import Counter
    a, b = Counter(ref.split()), Counter(out.split())
    return sum((a & b).values()) / max(1, sum(a.values()))


def position_match(ref_doc, out_doc) -> tuple[float, float | None]:
    """Share of reference words found on the same page within POS_TOL pt, median offset of matches."""
    total = hit = 0
    dists = []
    for i, rp in enumerate(ref_doc):
        rw = page_words(rp)
        total += len(rw)
        if i >= len(out_doc):
            continue
        pool: dict[str, list] = {}
        for t, x, y in page_words(out_doc[i], rp.rect):
            pool.setdefault(t, []).append((x, y))
        for t, x, y in rw:
            cands = pool.get(t)
            if not cands:
                continue
            j, d = min(((j, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) for j, (cx, cy) in enumerate(cands)), key=lambda a: a[1])
            if d <= POS_TOL * 4:
                dists.append(d)
                cands.pop(j)
                if d <= POS_TOL:
                    hit += 1
    return (hit / total if total else 0.0), (statistics.median(dists) if dists else None)


def page_similarity(ref_doc, out_doc) -> float:
    n = max(len(ref_doc), len(out_doc))
    sims = []
    for i in range(n):
        a = norm(ref_doc[i].get_text()) if i < len(ref_doc) else ""
        b = norm(out_doc[i].get_text()) if i < len(out_doc) else ""
        sims.append(1.0 if not a and not b else Indel.normalized_similarity(a, b))
    return sum(sims) / n


def md_text(path: Path) -> str:
    """Markdown with markup removed; word spacing kept."""
    s = path.read_text(encoding="utf-8")
    s = re.sub(r"</?[A-Za-z][^>]*>", " ", s)  # HTML tags only; keep text like <개정 2020.1.1.>
    return re.sub(r"[#*|>`\-]+", " ", s)


def main():
    names = sorted(p.stem for p in (BENCH / "samples").glob(f"*.{EXT}"))
    rows = []
    for name in names:
        ref = pymupdf.open(BENCH / "ref" / f"{name}.pdf")
        ref_raw = "\n".join(p.get_text() for p in ref)
        ref_text = norm(ref_raw)
        for tool in PDF_TOOLS:
            f = OUT / tool / f"{name}.pdf"
            row = {"name": name, "tool": tool, "ref_pages": len(ref), "ok": f.exists()}
            if f.exists():
                out = pymupdf.open(f)
                out_raw = "\n".join(p.get_text() for p in out)
                out_text = norm(out_raw)
                rec, prec = lcs_scores(ref_text, out_text)
                pos, med = position_match(ref, out)
                row.update(pages=len(out), text_recall=rec, bag_recall=bag_recall(ref_text, out_text), text_precision=prec,
                           word_recall=word_recall(ref_raw, out_raw), page_sim=page_similarity(ref, out),
                           pos_match=pos, pos_median_pt=med)
            rows.append(row)
        for tool in NATIVE_TOOLS:
            f = OUT / tool / f"{name}.md"
            row = {"name": name, "tool": tool, "ref_pages": len(ref), "ok": f.exists()}
            if f.exists():
                # markdown emphasis uses '*', so drop it on both sides (references contain '*' redaction masks)
                o_raw = md_text(f)
                r_txt, o_txt = ref_text.replace("*", ""), norm(o_raw)
                rec, prec = lcs_scores(r_txt, o_txt)
                row.update(text_recall=rec, text_precision=prec, bag_recall=bag_recall(r_txt, o_txt),
                           word_recall=word_recall(ref_raw.replace("*", ""), o_raw))
            rows.append(row)
    for tool in PDF_TOOLS + NATIVE_TOOLS:
        res = OUT / tool / "results.json"
        secs = {r["name"]: r.get("seconds") for r in json.loads(res.read_text())} if res.exists() else {}
        for r in rows:
            if r["tool"] == tool:
                r["seconds"] = secs.get(r["name"])
    (OUT / "eval.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    print(f"{'tool':12} ok  pages=  recall  prec  bag_rec word_rec  page_sim  pos@6pt  med_pt  sec/doc")
    for tool in PDF_TOOLS + NATIVE_TOOLS:
        rs = [r for r in rows if r["tool"] == tool]
        ok = [r for r in rs if r["ok"]]
        pm = sum(1 for r in ok if r.get("pages") == r["ref_pages"])
        f = lambda k: mean([r.get(k) for r in ok])
        fmt = lambda v, w=6: f"{v:{w}.3f}" if v is not None else " " * (w - 1) + "-"
        print(f"{tool:12} {len(ok):2}/{len(rs)} {pm:2}  {fmt(f('text_recall'))} {fmt(f('text_precision'))} {fmt(f('bag_recall'),7)} {fmt(f('word_recall'),8)} {fmt(f('page_sim'),8)} {fmt(f('pos_match'),8)} {fmt(f('pos_median_pt'),7)} {fmt(f('seconds'),7)}")
    print()
    print(f"{'name':18}" + "".join(f"{t:>26}" for t in PDF_TOOLS) + "".join(f"{t:>10}" for t in NATIVE_TOOLS))
    for name in names:
        line = f"{name:18}"
        for tool in PDF_TOOLS:
            r = next(r for r in rows if r["name"] == name and r["tool"] == tool)
            line += (f"  p{r['pages']:>3}/{r['ref_pages']:<3} rec{r['text_recall']:.2f} pos{r['pos_match']:.2f}" if r["ok"] else f"{'FAIL':>26}")
        for tool in NATIVE_TOOLS:
            r = next(r for r in rows if r["name"] == name and r["tool"] == tool)
            line += f"   rec{r['text_recall']:.2f}" if r["ok"] else "      FAIL"
        print(line)


if __name__ == "__main__":
    main()
