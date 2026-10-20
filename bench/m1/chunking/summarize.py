"""scores.json -> markdown table + paired-bootstrap vs a reference config.  usage: summarize.py scores.json [ref_config]"""
import json, sys
import numpy as np
res = json.load(open(sys.argv[1])); ref = sys.argv[2] if len(sys.argv) > 2 else "adaptive_max1024_min128"
rng = np.random.default_rng(0)
def col(c, k): return np.array([r[k] for r in res[c]["per_query"]])
print("| config | chunks | tok mean/p90/max | rec@1k | rec@2k | rec@4k | strict@2k | all-spans@2k | MRR | nDCG@10 | Δrec@2k vs ref [95% CI] |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for c, v in res.items():
    s = v["stats"]; d = col(c, "rec2000") - col(ref, "rec2000")
    b = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
    print(f"| {c} | {s['n_chunks']} | {s['tok_mean']:.0f}/{s['tok_p90']:.0f}/{s['tok_max']} | {col(c,'rec1000').mean():.3f} | {col(c,'rec2000').mean():.3f} | {col(c,'rec4000').mean():.3f} | {col(c,'strict2000').mean():.3f} | {col(c,'all2000').mean():.3f} | {col(c,'mrr').mean():.3f} | {col(c,'ndcg10').mean():.3f} | {d.mean():+.3f} [{np.percentile(b,2.5):+.3f}, {np.percentile(b,97.5):+.3f}] |")
