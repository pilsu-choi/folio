"""Eval: testset generation, retrieval metrics, run/compare per config commit.

Plain functions (the CLI wraps them as ``folio eval testset|run|compare``):

    testset.generate(chunks, n, method="heuristic"|"llm", llm_url=None, model=None, seed=0)
        -> [{"query", "relevant_chunk_ids", "doc_id"}]
    metrics.recall_at_k(relevant, ranked, k) / mrr(relevant, ranked) / ndcg_at_k(relevant, ranked, k)
    run(testset, retriever, ks=(1, 5, 10), config_dir=None, **search_kwargs) -> dict
        {"metrics": {"recall@1": .., "mrr": .., "ndcg@10": ..}, "n": int, "config_commit": sha|None, "timestamp", "ks"}
    compare(result_a, result_b) -> {"a": commit, "b": commit, "metrics": {name: {"a", "b", "delta"}}}
    save_result(result, out_dir) -> Path   # {out_dir}/{config_commit or timestamp}.json
    load_result(path) -> dict
"""

from folio.eval.run import compare, config_commit, load_result, run, save_result

__all__ = ["compare", "config_commit", "load_result", "run", "save_result"]
