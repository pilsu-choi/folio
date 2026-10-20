"""Model-free baseline backend: layout from word geometry, ruled tables from line detection.

OCR (`words=None`) needs `rapidocr_onnxruntime` (or `rapidocr`); everything else needs only Pillow
(numpy is used for ruled-table detection when importable).
"""

from __future__ import annotations

import io
import re
import statistics

from PIL import Image

from folio.backends.base import PageStructure, Region, Table, TableCell, Word

_BULLET = re.compile(r"^\s*(?:[-*•·▪○●■□◦▶※]|\(?\d{1,2}[.)]|[가-하][.)]|[①-⑩])\s*\S")


def _h(b) -> float:
    return b[3] - b[1]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


class HeuristicBackend:
    def __init__(self, heading_ratio: float = 1.2, gap_ratio: float = 0.7, column_gap_ratio: float = 3.0,
                 detect_tables: bool = True, ocr=None):
        self.heading_ratio = heading_ratio
        self.gap_ratio = gap_ratio
        self.column_gap_ratio = column_gap_ratio
        self.detect_tables = detect_tables
        self._ocr = ocr

    # ---- OCR -------------------------------------------------------------------------------
    def _get_ocr(self):
        if self._ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                try:
                    from rapidocr import RapidOCR
                except ImportError as exc:
                    raise RuntimeError(
                        "HeuristicBackend OCR needs `rapidocr_onnxruntime` (pip install rapidocr-onnxruntime); "
                        "digital pages work without it") from exc
            self._ocr = RapidOCR()
        return self._ocr

    def _run_ocr(self, img: Image.Image) -> list[Word]:
        import numpy as np

        out = self._get_ocr()(np.array(img.convert("RGB")))
        items = []
        if isinstance(out, tuple):  # rapidocr_onnxruntime: (result, elapse)
            items = [(b, t, float(s)) for b, t, s in (out[0] or [])]
        elif getattr(out, "boxes", None) is not None:  # rapidocr >= 2
            items = list(zip(out.boxes, out.txts, out.scores))
        words = []
        for box, text, score in items:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            if str(text).strip():
                words.append(Word(text=str(text), bbox=[min(xs), min(ys), max(xs), max(ys)], conf=float(score)))
        return words

    # ---- analyze ---------------------------------------------------------------------------
    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        img = Image.open(io.BytesIO(page_image))
        img.load()
        ocr_words = None
        if words is None:
            ocr_words = words = self._run_ocr(img)
        tables = self._find_tables(img) if self.detect_tables else []
        free = [w for w in words if not any(_inside(_center(w.bbox), t.bbox) for t in tables)]
        regions = self._layout(free, img.height)
        regions += [Region(type="table", bbox=t.bbox) for t in tables]
        return PageStructure(regions=regions, tables=tables, ocr_words=ocr_words,
                             meta={"backend": "heuristic"})

    # ---- layout ----------------------------------------------------------------------------
    def _lines(self, words: list[Word]) -> list[list[Word]]:
        lines: list[list[Word]] = []
        ycs: list[float] = []
        for w in sorted(words, key=lambda w: (w.bbox[1] + w.bbox[3]) / 2):
            yc = (w.bbox[1] + w.bbox[3]) / 2
            for i, ln in enumerate(lines):
                lh = statistics.median(_h(x.bbox) for x in ln)
                if abs(yc - ycs[i]) <= 0.5 * min(_h(w.bbox), lh):
                    ln.append(w)
                    ycs[i] = statistics.mean((x.bbox[1] + x.bbox[3]) / 2 for x in ln)
                    break
            else:
                lines.append([w])
                ycs.append(yc)
        return [sorted(ln, key=lambda w: w.bbox[0]) for _, ln in sorted(zip(ycs, lines), key=lambda p: p[0])]

    def _segments(self, lines: list[list[Word]]) -> list[dict]:
        segs = []
        for ln in lines:
            h = statistics.median(_h(w.bbox) for w in ln)
            cur = [ln[0]]
            for w in ln[1:]:
                if w.bbox[0] - cur[-1].bbox[2] > self.column_gap_ratio * h:
                    segs.append(self._seg(cur, h))
                    cur = []
                cur.append(w)
            segs.append(self._seg(cur, h))
        return segs

    @staticmethod
    def _seg(ws: list[Word], h: float) -> dict:
        return {"box": _union([w.bbox for w in ws]), "h": h, "text": " ".join(w.text for w in ws), "n": sum(len(w.text) for w in ws)}

    def _layout(self, words: list[Word], page_h: float) -> list[Region]:
        if not words:
            return []
        segs = self._segments(self._lines(words))
        body = statistics.median([s["h"] for s in segs for _ in range(max(s["n"], 1))])
        heading = lambda s: s["h"] >= self.heading_ratio * body
        paras: list[list[dict]] = []
        for s in sorted(segs, key=lambda s: (s["box"][1], s["box"][0])):
            bullet = bool(_BULLET.match(s["text"])) and not heading(s)
            best = None
            if not bullet:
                for p in paras:
                    last = p[-1]
                    gap = s["box"][1] - last["box"][3]
                    ov = min(s["box"][2], last["box"][2]) - max(s["box"][0], last["box"][0])
                    narrow = min(s["box"][2] - s["box"][0], last["box"][2] - last["box"][0])
                    if -0.5 * s["h"] <= gap <= self.gap_ratio * s["h"] and ov >= 0.4 * narrow \
                            and abs(s["h"] - last["h"]) <= 0.25 * last["h"]:
                        if best is None or gap < best[0]:
                            best = (gap, p)
            if best:
                best[1].append(s)
            else:
                s["bullet"] = bullet
                paras.append([s])
        regions = []
        for p in paras:
            box = _union([s["box"] for s in p])
            h = statistics.median([s["h"] for s in p])
            typ = "paragraph"
            if p[0].get("bullet"):
                typ = "list"
            elif h >= self.heading_ratio * body and len(p) <= 3:
                typ = "heading"
            elif len(p) == 1 and (box[3] < 0.05 * page_h or box[1] > 0.95 * page_h):
                typ = "header" if box[3] < 0.5 * page_h else "footer"
            regions.append(Region(type=typ, bbox=box))
        return regions

    # ---- ruled tables ----------------------------------------------------------------------
    def _find_tables(self, img: Image.Image) -> list[Table]:
        try:
            import numpy as np
        except ImportError:
            return []
        bw = np.array(img.convert("L")) < 140
        H, W = bw.shape
        minlen = max(int(0.04 * min(H, W)), 20)
        hs = _runs(bw, minlen)  # (y, x0, x1)
        vs = [(x, y0, y1) for x, y0, y1 in _runs(bw.T, minlen)]
        if len(hs) < 2 or len(vs) < 2:
            return []
        # connected components of intersecting segments
        parent = list(range(len(hs) + len(vs)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        t = 4
        for i, (y, x0, x1) in enumerate(hs):
            for j, (x, y0, y1) in enumerate(vs):
                if x0 - t <= x <= x1 + t and y0 - t <= y <= y1 + t:
                    parent[find(i)] = find(len(hs) + j)
        comps: dict[int, list[int]] = {}
        for i in range(len(parent)):
            comps.setdefault(find(i), []).append(i)
        tables = []
        for idx in comps.values():
            hh = [hs[i] for i in idx if i < len(hs)]
            vv = [vs[i - len(hs)] for i in idx if i >= len(hs)]
            if len(hh) < 2 or len(vv) < 2:
                continue
            ys = _cluster([y for y, _, _ in hh])
            xs = _cluster([x for x, _, _ in vv])
            if len(ys) < 2 or len(xs) < 2:
                continue
            tables.append(_build_table(bw, xs, ys))
        return tables


def _center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _inside(pt, b) -> bool:
    return b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def _runs(bw, minlen: int) -> list[tuple[float, int, int]]:
    """Horizontal dark runs >= minlen, thick lines merged -> (y, x0, x1)."""
    import numpy as np

    segs: list[list[float]] = []  # y_sum, count, x0, x1, last_y
    for y in np.nonzero(bw.sum(axis=1) >= minlen)[0]:
        row = np.concatenate(([0], bw[y].view(np.int8), [0]))
        d = np.diff(row)
        for a, b in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
            if b - a >= minlen:
                for s in segs:
                    if y - s[4] <= 1 and abs(s[2] - a) <= 3 and abs(s[3] - b) <= 3:
                        s[0] += y
                        s[1] += 1
                        s[4] = y
                        break
                else:
                    segs.append([y, 1, a, b, y])
    return [(s[0] / s[1], int(s[2]), int(s[3])) for s in segs]


def _cluster(vals: list[float], tol: float = 4.0) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [statistics.mean(g) for g in out]


def _edge_present(bw, horizontal: bool, pos: float, a: float, b: float) -> bool:
    """Is a ruling line present at `pos` spanning [a, b]? (80% coverage within +-3px)"""
    p0, p1 = int(pos) - 3, int(pos) + 4
    a0, a1 = int(a) + 3, int(b) - 3
    if a1 <= a0:
        return True
    sub = bw[max(p0, 0):p1, a0:a1] if horizontal else bw[a0:a1, max(p0, 0):p1].T
    return sub.size > 0 and sub.any(axis=0).mean() >= 0.8


def _build_table(bw, xs: list[float], ys: list[float]) -> Table:
    nr, nc = len(ys) - 1, len(xs) - 1
    parent = list(range(nr * nc))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r in range(nr):
        for c in range(nc):
            if c + 1 < nc and not _edge_present(bw, False, xs[c + 1], ys[r], ys[r + 1]):
                parent[find(r * nc + c)] = find(r * nc + c + 1)
            if r + 1 < nr and not _edge_present(bw, True, ys[r + 1], xs[c], xs[c + 1]):
                parent[find(r * nc + c)] = find((r + 1) * nc + c)
    groups: dict[int, list[tuple[int, int]]] = {}
    for r in range(nr):
        for c in range(nc):
            groups.setdefault(find(r * nc + c), []).append((r, c))
    cells = []
    for g in groups.values():
        r0, r1 = min(r for r, _ in g), max(r for r, _ in g)
        c0, c1 = min(c for _, c in g), max(c for _, c in g)
        if len(g) != (r1 - r0 + 1) * (c1 - c0 + 1):  # non-rectangular merge: keep unmerged
            for r, c in g:
                cells.append(TableCell(bbox=[xs[c], ys[r], xs[c + 1], ys[r + 1]], row=r, col=c))
            continue
        cells.append(TableCell(bbox=[xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1]], row=r0, col=c0,
                               rowspan=r1 - r0 + 1, colspan=c1 - c0 + 1))
    cells.sort(key=lambda c: (c.row, c.col))
    return Table(bbox=[xs[0], ys[0], xs[-1], ys[-1]], cells=cells)
