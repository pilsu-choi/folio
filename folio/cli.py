"""`folio` command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SUPPORTED = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
    ".pptx", ".xlsx", ".md", ".markdown", ".txt",
}


def _config_dir(args) -> str:
    return args.config_dir or os.environ.get("FOLIO_CONFIG_DIR") or os.getcwd()


def _pipeline_names(config_dir: str) -> list[str]:
    d = Path(config_dir) / "pipelines"
    return sorted(p.stem for p in d.glob("*.y*ml")) if d.is_dir() else []


def _load(args, name: str):
    from folio.pipeline import load_pipeline

    return load_pipeline(name, _config_dir(args))


def _cfgs(args):
    names = getattr(args, "pipeline", None) or _pipeline_names(_config_dir(args))
    if isinstance(names, str):
        names = [names]
    return [_load(args, n) for n in names]


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def cmd_init(args) -> int:
    from folio.pipeline import _sink, _storage

    for cfg in _cfgs(args):
        st = _storage(cfg)
        st.ensure()
        sink = _sink(cfg)
        info = sink.apply_schema() if hasattr(sink, "apply_schema") else {}
        _out({"pipeline": cfg.name, "storage": "ready", "schema": info})
    if os.environ.get("FOLIO_DATABASE_URL"):
        from service.jobs import JobStore

        JobStore().create_all()
        _out({"jobs_table": "ready"})
    return 0


def cmd_schema(args) -> int:
    for cfg in _cfgs(args):
        from folio.pipeline import _sink

        sink = _sink(cfg)
        if not hasattr(sink, "apply_schema"):
            _out({"pipeline": cfg.name, "schema": "unsupported"})
            continue
        _out({"pipeline": cfg.name, "schema": sink.apply_schema(reindex=args.reindex)})
    return 0


def _expand(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if "://" not in p and Path(p).is_dir():
            out += [str(f) for f in sorted(Path(p).rglob("*")) if f.suffix.lower() in SUPPORTED]
        else:
            out.append(p)
    return out


def cmd_run(args) -> int:
    from folio.pipeline import run_pipeline

    cfg = _load(args, args.pipeline)
    rc = 0
    for uri in _expand(args.paths):
        try:
            res = run_pipeline(uri, cfg, stop_after=args.stop_after)
            _out({"uri": uri, **res.to_dict()})
        except Exception as exc:
            rc = 1
            _out({"uri": uri, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return rc


def cmd_reprocess(args) -> int:
    from folio.pipeline import reprocess

    if args.pipeline:
        cfg = _load(args, args.pipeline)
    else:
        matches = [c for c in _cfgs(args) if c.index == args.index]
        if not matches:
            print(f"no pipeline with index {args.index!r}", file=sys.stderr)
            return 2
        cfg = matches[0]
    rc = 0
    try:
        for res in reprocess(cfg, args.start_from, args.doc_id or None):
            _out(res.to_dict())
    except Exception as exc:
        rc = 1
        _out({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return rc


def cmd_api(args) -> int:
    import uvicorn

    uvicorn.run("service.api:app", host=args.host, port=args.port)
    return 0


def cmd_worker(args) -> int:
    from service.worker import celery_app

    argv = ["worker", "--loglevel", os.environ.get("FOLIO_LOG_LEVEL", "INFO").lower(), "-O", "fair"]
    concurrency = args.concurrency or int(os.environ.get("FOLIO_WORKER_CONCURRENCY", "2"))
    argv += ["--concurrency", str(concurrency)]
    celery_app.worker_main(argv)
    return 0


def cmd_structure_server(args) -> int:
    from folio.backends.server import serve

    serve(port=args.port)
    return 0


def cmd_embed_stub(args) -> int:
    from folio.embed.stub import serve

    serve(port=args.port)
    return 0


def cmd_eval(args) -> int:
    from folio import eval as ev

    if args.eval_cmd == "testset":
        from folio.eval import testset

        if args.chunks:
            chunks = json.loads(Path(args.chunks).read_text(encoding="utf-8"))
        else:  # every stored chunks.json of the pipeline's index
            from folio.pipeline import _storage

            cfg = _load(args, args.pipeline)
            storage = _storage(cfg)
            chunks = [c for key in storage.list(f"{cfg.index}/") if key.endswith("/chunks.json")
                      for c in storage.get_json(key)]
        items = testset.generate(chunks, args.n, args.method, llm_url=args.llm_url, model=args.model)
        Path(args.out).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        _out({"testset": args.out, "items": len(items)})
    elif args.eval_cmd == "run":
        from folio._loader import load_callable
        from folio.retrieval import create_retriever

        cfg = _load(args, args.pipeline)
        embed = None
        if cfg.embed is not None:
            fn, opts = load_callable(cfg.embed.fn), cfg.embed.opts
            embed = lambda texts: fn(texts, **opts)
        items = json.loads(Path(args.testset).read_text(encoding="utf-8"))
        retriever = create_retriever(cfg.sink, _config_dir(args), embed)
        result = ev.run(items, retriever, tuple(int(k) for k in args.ks.split(",")),
                        config_dir=_config_dir(args), mode=args.mode)
        path = ev.save_result(result, args.out)
        _out({"result": str(path), "metrics": result["metrics"]})
    else:
        _out(ev.compare(ev.load_result(args.a), ev.load_result(args.b)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="folio")
    p.add_argument("--config-dir", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--pipeline", action="append")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("schema")
    ss = s.add_subparsers(dest="schema_cmd", required=True)
    a = ss.add_parser("apply")
    a.add_argument("--pipeline", action="append")
    a.add_argument("--reindex", action="store_true")
    a.set_defaults(fn=cmd_schema)

    s = sub.add_parser("run")
    s.add_argument("paths", nargs="+")
    s.add_argument("--pipeline", required=True)
    s.add_argument("--stop-after", choices=["parse", "chunk", "map", "embed", "index"])
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("reprocess")
    s.add_argument("--from", dest="start_from", required=True, choices=["parse", "chunk", "map"])
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--index")
    g.add_argument("--pipeline")
    s.add_argument("--doc-id", action="append")
    s.set_defaults(fn=cmd_reprocess)

    s = sub.add_parser("api")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_api)

    s = sub.add_parser("worker")
    s.add_argument("--concurrency", type=int)
    s.set_defaults(fn=cmd_worker)

    s = sub.add_parser("structure-server")
    s.add_argument("--port", type=int, default=9000)
    s.set_defaults(fn=cmd_structure_server)

    s = sub.add_parser("embed-stub")
    s.add_argument("--port", type=int, default=8001)
    s.set_defaults(fn=cmd_embed_stub)

    s = sub.add_parser("eval")
    es = s.add_subparsers(dest="eval_cmd", required=True)
    t = es.add_parser("testset")
    src = t.add_mutually_exclusive_group(required=True)
    src.add_argument("--chunks", help="chunks.json (list of chunk records)")
    src.add_argument("--pipeline", help="use every stored chunks.json of this pipeline's index")
    t.add_argument("--n", type=int, default=50)
    t.add_argument("--method", choices=["heuristic", "llm"], default="heuristic")
    t.add_argument("--llm-url", default=None, help="OpenAI-compatible base URL for --method llm")
    t.add_argument("--model", default=None)
    t.add_argument("--out", default="testset.json")
    r = es.add_parser("run")
    r.add_argument("--pipeline", required=True)
    r.add_argument("--testset", required=True)
    r.add_argument("--ks", default="1,5,10")
    r.add_argument("--mode", choices=["hybrid", "bm25", "vector"], default="hybrid")
    r.add_argument("--out", default="eval-out")
    c = es.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    s.set_defaults(fn=cmd_eval)

    sub.add_parser("cli", help="passthrough alias: `folio cli init` == `folio init`")  # stripped in main()
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, tok in enumerate(argv):  # `folio [--config-dir D] cli ARGS` == `folio [--config-dir D] ARGS`
        if tok == "cli" and (i == 0 or argv[i - 1] != "--config-dir"):
            argv = argv[:i] + argv[i + 1 :]
            break
        if not tok.startswith("-") and (i == 0 or argv[i - 1] != "--config-dir"):
            break
    args = build_parser().parse_args(argv)
    rc = args.fn(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
