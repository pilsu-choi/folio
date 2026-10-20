"""Convert server (`folio convert-server`): local LibreOffice/rhwp behind `POST /v1/convert` (+ /healthz, /readyz)."""

import os
import shutil
import threading

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from folio.parse import convert as cv


def create_app(workers: int | None = None, timeout: float | None = None):
    """`POST /v1/convert` (multipart `file`, optional form `mime`) -> PDF bytes; local converters only.
    At most FOLIO_CONVERT_WORKERS (default 2) conversions run at once; others wait their turn."""
    workers = int(workers or os.environ.get("FOLIO_CONVERT_WORKERS") or 2)
    timeout = float(timeout or os.environ.get("FOLIO_CONVERT_TIMEOUT") or 300)
    slots = threading.BoundedSemaphore(workers)
    app = FastAPI(title="folio convert server")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        tools = {"libreoffice": cv._version("libreoffice") if cv._soffice() else None,
                 "rhwp": cv._version("rhwp") if shutil.which("rhwp") else None, "h2orestart": cv._has_h2orestart()}
        ok = bool(tools["libreoffice"] or tools["rhwp"])
        body = {"status": "ready" if ok else "error", "workers": workers, **tools}
        return body if ok else JSONResponse(body, status_code=503)

    @app.post("/v1/convert")
    def convert(file: UploadFile = File(...), mime: str | None = Form(None)):
        mime = mime or file.content_type or ""
        if mime not in cv.CHAINS:
            raise HTTPException(415, f"unsupported mime: {mime}")
        data = file.file.read()
        with slots:
            try:
                pdf, info = cv.convert_local(data, mime, timeout)
            except cv.ConvertError as exc:
                raise HTTPException(422, str(exc)) from exc
        return Response(pdf, media_type="application/pdf",
                        headers={"x-folio-converter": info["converter"],
                                 "x-folio-converter-version": info.get("version") or ""})

    return app


def serve(host: str = "0.0.0.0", port: int = 9100) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
