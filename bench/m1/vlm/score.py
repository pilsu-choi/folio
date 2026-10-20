"""Score scan + table tasks (no GPU). usage: score.py <outdir> -> prints JSON summary to <outdir>/scores.json"""
import difflib, glob, html, json, os, re, sys
from html.parser import HTMLParser
S = os.path.expanduser("~/folio-m1-bench/samples")

def ws(s): return re.sub(r"\s+", "", s)
def norm_scan(s):
    s = re.sub(r"^\s*\|?[\s:\-|]+\|?\s*$", "", s, flags=re.M)      # md table separator rows
    s = re.sub(r"<[^>]+>", "", s)                                  # stray html tags
    s = re.sub(r"[#|*`]", "", s)                                   # md syntax
    return ws(s)

def lev(a, b):
    if len(a) < len(b): a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def scan_score(pred, truth_lines):
    t, p = ws("".join(truth_lines)), norm_scan(pred)
    sm = difflib.SequenceMatcher(None, t, p, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    ratio = sm.ratio()
    cer = lev(t, p) / max(1, len(t))
    ins = 1 - matched / max(1, len(p))           # share of output chars not found in truth ~ hallucination/noise
    loop = bool(re.search(r"(.{6,40}?)\1{5,}", p))
    return dict(ratio=round(ratio, 4), cer=round(cer, 4), extra=round(ins, 4), loop=loop)

# ---------- table parsing -> dense grid with merged text at top-left, covered cells "" ----------
class HP(HTMLParser):
    def __init__(s): super().__init__(); s.rows = []; s.cur = None; s.cell = None
    def handle_starttag(s, tag, a):
        a = dict(a)
        if tag == "tr": s.cur = []; s.rows.append(s.cur)
        elif tag in ("td", "th"):
            if s.cur is None: s.cur = []; s.rows.append(s.cur)
            s.cell = {"t": "", "rs": int(re.sub(r"\D", "", a.get("rowspan", "1")) or 1), "cs": int(re.sub(r"\D", "", a.get("colspan", "1")) or 1)}
            s.cur.append(s.cell)
        elif tag == "br" and s.cell is not None: s.cell["t"] += " "
    def handle_endtag(s, tag):
        if tag in ("td", "th"): s.cell = None
        if tag == "tr": s.cur = None
    def handle_data(s, d):
        if s.cell is not None: s.cell["t"] += d

def grid_from_cells(rows):  # rows: list of list of {t,rs,cs}
    occ = {}; spans = []; R = 0; C = 0
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in occ: c += 1
            cell["rs"] = max(1, min(cell["rs"], 50)); cell["cs"] = max(1, min(cell["cs"], 50))
            for i in range(cell["rs"]):
                for j in range(cell["cs"]): occ[(r + i, c + j)] = cell["t"] if (i == 0 and j == 0) else ""
            if cell["rs"] > 1 or cell["cs"] > 1: spans.append((r, c, cell["rs"], cell["cs"]))
            R = max(R, r + cell["rs"]); C = max(C, c + cell["cs"]); c += cell["cs"]
    return [[occ.get((r, c), "") for c in range(C)] for r in range(R)], spans

def parse_html(s):
    p = HP(); p.feed(s)
    return grid_from_cells(p.rows) if p.rows else None

def parse_otsl(s):  # PaddleOCR-VL table output: <fcel>text <lcel> <ucel> <xcel> <ecel> <nl>
    if "<fcel>" not in s and "<nl>" not in s: return None
    m0 = re.search(r"<(fcel|ecel)>", s)
    pre = s[:m0.start()]
    pre_cell = pre.split("\n")[-1].strip()          # title lines before the table are dropped; a tagless first cell is kept
    s = ("<fcel>" + pre_cell if pre_cell else "") + s[m0.start():]
    lines = [l for l in re.split(r"<nl>", s) if l.strip()]
    grid = []
    for l in lines:
        toks = re.findall(r"<(fcel|ecel|lcel|ucel|xcel)>([^<]*)", l)
        grid.append(toks)
    R = len(grid); C = max((len(r) for r in grid), default=0)
    g = [[("fcel", "")] * 0 for _ in range(R)]
    spans = []; out = [["" for _ in range(C)] for _ in range(R)]
    for r in range(R):
        for c, (k, t) in enumerate(grid[r]):
            if k == "fcel": out[r][c] = t.strip()
    for r in range(R):
        for c, (k, t) in enumerate(grid[r]):
            if k == "fcel":
                cs = 1
                while c + cs < len(grid[r]) and grid[r][c + cs][0] == "lcel": cs += 1
                rs = 1
                while r + rs < R and c < len(grid[r + rs]) and grid[r + rs][c][0] in ("ucel", "xcel"): rs += 1
                if cs > 1 or rs > 1: spans.append((r, c, rs, cs))
    return out, spans

def parse_md(s):
    rows = []
    for l in s.splitlines():
        if "|" in l:  # lenient: also accepts rows without outer pipes (production regex requires them; see strict_md)
            if re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", l): continue
            rows.append([{"t": c.strip(), "rs": 1, "cs": 1} for c in l.strip().strip("|").split("|")])
    if not rows: return None
    g, sp = grid_from_cells(rows)
    return g, []

def parse_table(s):
    s = re.sub(r"```\w*", "", s)
    for f in (parse_otsl, parse_html if "<t" in s.lower() else None, parse_md):
        if f is None: continue
        r = f(s)
        if r and r[0]: return (f.__name__[6:],) + r
    return None

def cn(s): return ws(html.unescape(s))

def table_score(pred, truth):
    pr = parse_table(pred)
    R, C = truth["rows"], truth["cols"]
    if not pr: return dict(parsed=False, rows_ok=False, cols_ok=False, cell_acc=0.0, span_f1=0.0, fmt=None, merge_repr=None)
    fmt, g, sp = pr
    pR, pC = len(g), max(len(r) for r in g)
    ok = tot = 0
    for r in range(R):
        for c in range(C):
            tot += 1
            if r < pR and c < len(g[r]) and cn(g[r][c]) == cn(truth["cells"][r][c]): ok += 1
    ts = {(s["row"], s["col"], s["rowspan"], s["colspan"]) for s in truth["spans"]}
    ps = set(sp)
    tp = len(ts & ps)
    f1 = 2 * tp / (len(ts) + len(ps)) if (ts or ps) else 1.0
    # how are merged cells represented? look at covered cells of truth spans in prediction
    rep = {"span_attr": 0, "empty": 0, "dup": 0, "other": 0}
    for (r, c, rs, cs) in ts:
        if (r, c, rs, cs) in ps: rep["span_attr"] += 1; continue
        top = cn(truth["cells"][r][c]); covered = [(r + i, c + j) for i in range(rs) for j in range(cs) if (i or j)]
        vals = [cn(g[a][b]) if a < pR and b < len(g[a]) else None for a, b in covered]
        if all(v == "" for v in vals): rep["empty"] += 1
        elif any(v == top and top for v in vals): rep["dup"] += 1
        else: rep["other"] += 1
    return dict(parsed=True, strict_md=bool(re.search(r"^\s*\|.*\|\s*$", pred, re.M)), rows_ok=pR == R, cols_ok=pC == C, cell_acc=round(ok / tot, 4), span_f1=round(f1, 4), fmt=fmt, dims=f"{pR}x{pC}", merge_repr=rep)

def main():
    out = os.path.expanduser(sys.argv[1]); res = {}
    for md in sorted(glob.glob(f"{out}/*/")):
        model = os.path.basename(md.rstrip("/")); res[model] = {}
        for f in sorted(glob.glob(f"{md}/*__*.json")):
            d = json.load(open(f)); task = d["task"]; i = d["id"]
            if task.startswith("scan") and i.startswith("s"):
                t = json.load(open(f"{S}/truth/{i}.truth.json"))["lines"]
                r = scan_score(d["text"], t)
            elif task.startswith("table"):
                r = table_score(d["text"], json.load(open(f"{S}/truth/{i}.table.json")))
            else: continue
            r.update(sec=d["sec"], finish=d.get("finish"), err=d.get("error"))
            res[model][f"{task}__{i}"] = r
    json.dump(res, open(f"{out}/scores.json", "w"), ensure_ascii=False, indent=1)
    # aggregate
    for model, rr in res.items():
        for task in sorted({k.split("__")[0] for k in rr}):
            xs = [v for k, v in rr.items() if k.startswith(task + "__")]
            n = len(xs); m = lambda key: sum(float(x.get(key) or 0) for x in xs) / n
            if task.startswith("scan"):
                print(model, task, n, "ratio %.3f cer %.3f extra %.3f loops %d sec %.1f" % (m("ratio"), m("cer"), m("extra"), sum(x["loop"] for x in xs), m("sec")))
            else:
                print(model, task, n, "rows_ok %d cols_ok %d cell_acc %.3f span_f1 %.3f unparsed %d strict_md_tables %d sec %.1f" % (sum(x["rows_ok"] for x in xs), sum(x["cols_ok"] for x in xs), m("cell_acc"), m("span_f1"), sum(not x["parsed"] for x in xs), sum(bool(x.get("strict_md")) for x in xs), m("sec")))
main()
