"""Eval: span-based testset generation, retrieval metrics, run/compare per config commit.

Plain functions (the CLI wraps them as ``folio eval testset|run|compare``):

    testset.generate(docs, pages=50, pairs=20, method="llm"|"heuristic", types=testset.TYPES, min_per_type=0, ...)
        -> (items, stats)   # a page pair is asked for one type; min_per_type tops up short types on unused pages
        item = {"qid", "type", "query", "answer", "evidence": [{"doc_id", "page", "text", "start", "end"}]}
        types: fact, table, section (one page); multi_hop, comparison, temporal, multi_intent (two pages of a
        document); multi_document (pages of two documents)
    metrics.recall_at_k / all_at_k / mrr / ndcg_at_k over per-rank span coverage
    run(testset, retriever, ks=(1, 5, 10), config_dir=None, chunks=None, **search_kwargs) -> dict
        {"metrics": {"recall@5": .., "all@5": .., "mrr": .., "ndcg@10": ..}, "by_type": {type: {"n", "metrics"}},
         "per_query": {qid: {...}}, "unreachable": [qid] (with chunks), "n", "ks", "config_commit", "timestamp"}
    compare(result_a, result_b) -> {"a", "b", "n", "metrics": {name: {"a", "b", "delta", "ci95"}}, "by_type": ...}
    save_result(result, out_dir) -> Path   # {out_dir}/{config_commit or timestamp}.json
    load_result(path) -> dict

Evidence is source text, not chunk ids, so one testset compares any chunking config: a retrieved chunk covers a
span when it holds a 20-char window of it (or the span straddles the chunk edge by >= 6 chars) on the same doc/page.
"""

from folio.eval.run import compare, config_commit, load_result, run, save_result

__all__ = ["compare", "config_commit", "load_result", "run", "save_result"]
