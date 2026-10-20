"""Office/HWP -> PDF conversion for the `convert` modes (FR-20 DOCX·HWPX·HWP, PPTX `pptx_mode="convert"`).

Converter choice (`converter` parse option, default "auto"):
  "auto"   -> the convert server when FOLIO_CONVERT_URL is set, else local binaries
  "local"  -> local binaries in `CHAINS` order (DOCX/PPTX: LibreOffice; HWPX/HWP: rhwp, then LO+H2Orestart)
  {"url", "timeout"} | "http" -> convert server (`folio convert-server`, folio.parse.convert_server)
  "module:function" | callable(data, mime) -> PDF bytes (or (PDF bytes, info dict))
The returned info goes to Document `meta.convert`; bbox values are only valid for that exact PDF (`pdf_hash`).
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import httpx

from folio._loader import load_callable
from folio.contracts import content_hash
from folio.errors import FolioError
from folio.logging import clip, log_event, ms_since, redact_url

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MIME_HWPX = "application/hwp+zip"
MIME_HWP = "application/x-hwp"
EXT = {MIME_DOCX: "docx", MIME_PPTX: "pptx", MIME_HWPX: "hwpx", MIME_HWP: "hwp"}
CHAINS = {MIME_DOCX: ["libreoffice"], MIME_PPTX: ["libreoffice"],
          MIME_HWPX: ["rhwp", "libreoffice"], MIME_HWP: ["rhwp", "libreoffice"]}
_LO_FILTER = {MIME_DOCX: "pdf:writer_pdf_Export", MIME_PPTX: "pdf:impress_pdf_Export",
              MIME_HWPX: "pdf:writer_pdf_Export", MIME_HWP: "pdf:writer_pdf_Export"}


class ConvertError(FolioError):
    """No converter is available for the format, or every converter in the chain failed."""


def _run(cmd: list[str], timeout: float) -> None:
    """Run in its own process group so a timeout also kills children (soffice.bin keeps the profile locked)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        raise ConvertError(f"{Path(cmd[0]).name} timed out after {timeout:.0f}s") from None
    if p.returncode != 0:
        raise ConvertError(f"{Path(cmd[0]).name} exit {p.returncode}: {out.decode(errors='replace')[-500:]}")


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


@cache
def _has_h2orestart() -> bool:
    """H2Orestart (GPL, optional) is the only LibreOffice import filter for HWPX."""
    exe = _soffice()
    if not exe:
        return False
    root = Path(os.path.realpath(exe)).parent.parent  # .../lib/libreoffice
    home = Path(os.path.expanduser("~"))
    return any(glob.glob(str(d / "**" / "H2Orestart*"), recursive=True)
               for d in (root / "share" / "uno_packages", home / ".config" / "libreoffice"))


@cache
def _version(name: str) -> str | None:
    exe = {"libreoffice": _soffice(), "rhwp": shutil.which("rhwp")}[name]
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def libreoffice(data: bytes, mime: str, timeout: float) -> bytes:
    exe = _soffice()
    if not exe:
        raise ConvertError("LibreOffice (soffice) not found; install it or set FOLIO_CONVERT_URL")
    if mime in (MIME_HWPX, MIME_HWP) and not _has_h2orestart():
        raise ConvertError("LibreOffice cannot open HWP/HWPX without the H2Orestart extension")
    with tempfile.TemporaryDirectory(prefix="folio-lo-") as d:
        src = Path(d) / f"in.{EXT[mime]}"
        src.write_bytes(data)
        # a private profile per call: concurrent soffice runs sharing one profile fail or exit silently
        _run([exe, f"-env:UserInstallation=file://{d}/profile", "--headless", "--norestore",
              "--convert-to", _LO_FILTER[mime], "--outdir", d, str(src)], timeout)
        out = Path(d) / "in.pdf"
        if not out.is_file() or out.stat().st_size == 0:
            raise ConvertError("LibreOffice produced no PDF")
        return out.read_bytes()


def rhwp(data: bytes, mime: str, timeout: float) -> bytes:
    exe = shutil.which("rhwp")
    if not exe:
        raise ConvertError("rhwp not found; install it or set FOLIO_CONVERT_URL")
    with tempfile.TemporaryDirectory(prefix="folio-rhwp-") as d:
        src, out = Path(d) / f"in.{EXT[mime]}", Path(d) / "out.pdf"
        src.write_bytes(data)
        fonts = os.environ.get("FOLIO_CONVERT_FONT_PATH")
        _run([exe, "export-pdf", str(src), "-o", str(out), *(["--font-path", fonts] if fonts else [])], timeout)
        if not out.is_file() or out.stat().st_size == 0:
            raise ConvertError("rhwp produced no PDF")
        return out.read_bytes()


LOCAL = {"libreoffice": libreoffice, "rhwp": rhwp}


def convert_local(data: bytes, mime: str, timeout: float = 300) -> tuple[bytes, dict]:
    """Try `CHAINS[mime]` in order; the first PDF wins. Failures of earlier converters are kept in info."""
    if mime not in CHAINS:
        raise ConvertError(f"no PDF converter for {mime}")
    errors = []
    for name in CHAINS[mime]:
        try:
            pdf = LOCAL[name](data, mime, timeout)
            return pdf, {"converter": name, "version": _version(name), **({"errors": errors} if errors else {})}
        except ConvertError as exc:
            errors.append(f"{name}: {exc}")
            log_event("convert_fallback", logging.WARNING, "folio.parse", converter=name, mime=EXT[mime], error=clip(exc, 300))
    raise ConvertError(f"cannot convert {EXT[mime]} to PDF: " + "; ".join(errors))


def _http(url: str, timeout: float, client: httpx.Client | None = None) -> Callable[[bytes, str], tuple]:
    url = url.rstrip("/")

    def call(data: bytes, mime: str) -> tuple[bytes, dict]:
        t0 = time.perf_counter()
        post = client.post if client is not None else httpx.post
        files = {"file": (f"in.{EXT.get(mime, 'bin')}", data, mime)}
        try:
            r = post(f"{url}/v1/convert", files=files, data={"mime": mime}, timeout=timeout + 30)
        except httpx.HTTPError as exc:
            raise ConvertError(f"convert server {url}: {type(exc).__name__}: {exc}") from exc
        if r.status_code != 200:
            raise ConvertError(f"convert server {url}: {r.status_code} {r.text[:500]}")
        log_event("convert_http", logging.DEBUG, "folio.parse", url=redact_url(url), mime=EXT.get(mime), in_bytes=len(data),
                  out_bytes=len(r.content), status=r.status_code, ms=ms_since(t0))
        info = {"converter": r.headers.get("x-folio-converter"), "version": r.headers.get("x-folio-converter-version"),
                "server": url}
        return r.content, info

    return call


def resolve(spec: Any = "auto", timeout: float | None = None) -> Callable[[bytes, str], tuple[bytes, dict]]:
    """`converter` option -> call(data, mime) -> (pdf, info). See module docstring."""
    timeout = float(timeout or os.environ.get("FOLIO_CONVERT_TIMEOUT") or 300)
    if spec in ("auto", None):
        spec = "http" if os.environ.get("FOLIO_CONVERT_URL") else "local"
    if spec == "local":
        return lambda data, mime: convert_local(data, mime, timeout)
    if spec == "http" or isinstance(spec, dict):
        cfg = {} if spec == "http" else dict(spec)
        url = cfg.get("url") or os.environ.get("FOLIO_CONVERT_URL")
        if not url:
            raise ConvertError("converter http requires url (or FOLIO_CONVERT_URL)")
        return _http(url, float(cfg.get("timeout", timeout)), cfg.get("client"))
    fn = load_callable(spec) if isinstance(spec, str) else spec
    if not callable(fn):
        raise ConvertError(f"invalid converter: {spec!r}")
    name = spec if isinstance(spec, str) else getattr(fn, "__name__", "custom")

    def call(data: bytes, mime: str) -> tuple[bytes, dict]:
        res = fn(data, mime)
        pdf, info = res if isinstance(res, tuple) else (res, {})
        return pdf, {"converter": name, **info}

    return call


def to_pdf(data: bytes, mime: str, spec: Any = "auto", timeout: float | None = None) -> tuple[bytes, dict]:
    """Convert and describe the result for `meta.convert` (converter, version, ms, pdf_hash)."""
    t0 = time.perf_counter()
    pdf, info = resolve(spec, timeout)(data, mime)
    if not pdf.startswith(b"%PDF-"):
        raise ConvertError(f"converter {info.get('converter')} did not return a PDF")
    log_event("convert", logging.DEBUG, "folio.parse", mime=EXT.get(mime), converter=info.get("converter"),
              in_bytes=len(data), pdf_bytes=len(pdf), ms=ms_since(t0))
    return pdf, {**info, "ms": round((time.perf_counter() - t0) * 1000, 1), "pdf_hash": content_hash(pdf)}
