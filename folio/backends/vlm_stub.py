"""Deterministic OpenAI-compatible chat server standing in for the VLM in dev/tests/Compose.

  python -m folio.backends.vlm_stub  (or `folio.backends.vlm_stub:serve`)  ->  POST /v1/chat/completions

The task is read from the prompt (same prompts as `folio.parse.vlm.PROMPTS`): pages -> OCR text as Markdown
(rapidocr when installed, else fixed text), figures -> "Figure: WxH image", tables -> a fixed GFM table.
"""

from __future__ import annotations

import base64
import io
import os

from fastapi import FastAPI, Request
from PIL import Image

FIXED_PAGE = "# Stub page\n\nStub VLM output for this page."


def _image(messages: list[dict]) -> tuple[str, Image.Image | None]:
    text, im = "", None
    for m in messages:
        c = m.get("content")
        for part in c if isinstance(c, list) else [{"type": "text", "text": c or ""}]:
            if part.get("type") == "text":
                text += part["text"] + "\n"
            elif part.get("type") == "image_url":
                url = part["image_url"]["url"]
                try:
                    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
                except Exception:
                    im = None
    return text, im


def _ocr_markdown(im: Image.Image) -> str:
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return FIXED_PAGE
    res, _ = _engine(RapidOCR)(np.array(im))
    lines = sorted(((b[0][1], b[0][0], t) for b, t, _ in res or []))
    return "\n\n".join(t for _, _, t in lines) or FIXED_PAGE


_ENGINE = []


def _engine(cls):
    if not _ENGINE:
        _ENGINE.append(cls())
    return _ENGINE[0]


def answer(prompt: str, im: Image.Image | None) -> str:
    p = prompt.lower()
    if "describe this figure" in p:
        return f"Figure: {im.width}x{im.height} image" if im else "Figure: image"
    if "convert this table" in p:
        return "| col1 | col2 |\n| --- | --- |\n| a | 1 |\n| b | 2 |"
    return _ocr_markdown(im) if im else FIXED_PAGE


def create_app() -> FastAPI:
    app = FastAPI(title="folio vlm stub")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": os.environ.get("FOLIO_VLM_MODEL", "folio-vlm"), "object": "model"}]}

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        prompt, im = _image(body.get("messages", []))
        return {"id": "stub", "object": "chat.completion", "model": body.get("model", "folio-vlm"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer(prompt, im)}}]}

    return app


app = create_app()


def serve(host: str = "0.0.0.0", port: int = 8002) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "8002")))
