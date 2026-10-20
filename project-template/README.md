# folio project template

    schemas/opensearch/contracts.json   index settings (nori analyzer, 384-dim knn_vector)
    schemas/milvus/contracts.json       Milvus collection definition (format: folio/sinks/milvus.py docstring)
    pipelines/contracts.yaml            stages as module:function; `map: mappers:map_contract`
    mappers.py                          domain fields (contract_no, party, article)
    samples/                            put sample documents here (PDF, images, PPTX, XLSX, Markdown, TXT)
    dags/folio_ingest.py                Airflow example (PythonOperator -> run_pipeline)
    Dockerfile                          FROM folio image + COPY project to /app/project

Usage: `export FOLIO_CONFIG_DIR=$PWD; folio init; folio run samples/ --pipeline contracts`.
Image: `docker build --build-arg FOLIO_IMAGE=folio:0.1.0 -t my-folio .`
Embedding dimension must match the schema (384); a mismatch fails before any write.
