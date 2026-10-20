"""DOCX·HWPX convert mode (FR-20): conversion -> regular PDF path. Real converters run only when installed."""

from __future__ import annotations

import io
import shutil
import time
import zipfile

import pymupdf
import pytest

from folio.contracts import content_hash
from folio.parse import convert as cv
from folio.parse import default
from folio.parse.convert import MIME_DOCX, MIME_HWP, MIME_HWPX, ConvertError
from tests._parse_helpers import FakeBackend, validate_doc

LINES = ["Convert mode heading", "First paragraph of the converted document.", "Second paragraph here."]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in ("FOLIO_VLM_URL", "FOLIO_CONVERT_URL", "FOLIO_STRUCTURE_URL"):
        monkeypatch.delenv(k, raising=False)


def office_zip(marker: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(marker, "<x/>")
    return buf.getvalue()


def fake_pdf(lines=LINES) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 24 * i), line, fontsize=12)
    return doc.tobytes()


def fake_converter(data: bytes, mime: str) -> bytes:
    fake_converter.seen = mime
    return fake_pdf()


@pytest.mark.parametrize("ext,marker,mime", [("docx", "word/document.xml", MIME_DOCX),
                                             ("hwpx", "Contents/header.xml", MIME_HWPX),
                                             ("hwp", None, MIME_HWP)])
def test_convert_mode_produces_pages_and_bbox(tmp_path, ext, marker, mime):
    src = tmp_path / f"a.{ext}"
    src.write_bytes(office_zip(marker) if marker else b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 hwp")
    doc = default(str(src), converter=fake_converter, backend=FakeBackend())
    assert fake_converter.seen == mime
    # identity comes from the original bytes; coordinates from the converted PDF
    assert doc.mime_type == mime and doc.content_hash == content_hash(src.read_bytes())
    conv = doc.meta["convert"]
    assert conv["converter"] == "fake_converter" and conv["pdf_hash"].startswith("sha256:")
    assert conv["ms"] >= 0
    assert len(doc.pages) == 1 and doc.pages[0].width == pytest.approx(595.0, abs=1)
    blocks = [b for _, b in doc.iter_blocks()]
    assert " ".join(b.text for b in blocks).split() == " ".join(LINES).split()
    assert all(b.bbox and b.text_source == "pdf_text" for b in blocks)
    validate_doc(doc)


def test_hwpx_detected_without_extension(tmp_path):
    src = tmp_path / "blob"
    src.write_bytes(office_zip("Contents/header.xml"))
    doc = default(str(src), converter=fake_converter, backend=FakeBackend())
    assert doc.mime_type == MIME_HWPX


def test_native_mode_is_fr21_and_bad_mode(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(office_zip("word/document.xml"))
    with pytest.raises(NotImplementedError, match="FR-21"):
        default(str(src), docx_mode="native")
    with pytest.raises(ValueError, match="mode"):
        default(str(src), docx_mode="pdf")


def test_converter_must_return_pdf(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(office_zip("word/document.xml"))
    with pytest.raises(ConvertError, match="did not return a PDF"):
        default(str(src), converter=lambda data, mime: b"nope")


def test_local_chain_falls_back_and_records_errors(monkeypatch):
    def fail(data, mime, timeout):
        raise ConvertError("boom")

    monkeypatch.setitem(cv.LOCAL, "rhwp", fail)
    monkeypatch.setitem(cv.LOCAL, "libreoffice", lambda data, mime, timeout: fake_pdf())
    monkeypatch.setattr(cv, "_version", lambda name: f"{name} 1.0")
    pdf, info = cv.to_pdf(b"x", MIME_HWPX, "local")
    assert pdf.startswith(b"%PDF-")
    assert info["converter"] == "libreoffice" and info["version"] == "libreoffice 1.0"
    assert info["errors"] == ["rhwp: boom"]
    monkeypatch.setitem(cv.LOCAL, "libreoffice", fail)
    with pytest.raises(ConvertError, match="rhwp: boom; libreoffice: boom"):
        cv.to_pdf(b"x", MIME_HWPX, "local")
    with pytest.raises(ConvertError, match="no PDF converter"):
        cv.convert_local(b"x", "text/plain")


def test_timeout_kills_process_group():
    t0 = time.perf_counter()
    with pytest.raises(ConvertError, match="timed out"):
        cv._run(["sh", "-c", "sleep 30 & sleep 30"], timeout=0.5)
    assert time.perf_counter() - t0 < 5


def test_server_roundtrip_and_auto_uses_url(monkeypatch, tmp_path):
    testclient = pytest.importorskip("fastapi.testclient")
    from folio.parse.convert_server import create_app

    monkeypatch.setattr(cv, "convert_local",
                        lambda data, mime, timeout: (fake_pdf(), {"converter": "libreoffice", "version": "LO 9"}))
    client = testclient.TestClient(create_app(workers=1))
    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/convert", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 415
    monkeypatch.setenv("FOLIO_CONVERT_URL", "http://testserver")
    src = tmp_path / "a.docx"
    src.write_bytes(office_zip("word/document.xml"))
    doc = default(str(src), converter={"client": client}, backend=FakeBackend())
    conv = doc.meta["convert"]
    assert conv["converter"] == "libreoffice" and conv["version"] == "LO 9" and conv["server"] == "http://testserver"
    assert len(doc.pages) == 1


def test_server_maps_convert_errors_to_422(monkeypatch):
    testclient = pytest.importorskip("fastapi.testclient")

    def fail(data, mime, timeout):
        raise ConvertError("cannot convert")

    from folio.parse.convert_server import create_app

    monkeypatch.setattr(cv, "convert_local", fail)
    client = testclient.TestClient(create_app(workers=1))
    r = client.post("/v1/convert", files={"file": ("a.docx", b"x", MIME_DOCX)}, data={"mime": MIME_DOCX})
    assert r.status_code == 422 and "cannot convert" in r.text
    with pytest.raises(ConvertError, match="422"):
        cv.resolve({"url": "http://testserver", "client": client})(b"x", MIME_DOCX)


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")), reason="LibreOffice not installed")
def test_real_libreoffice_docx(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("변환 시험", 1)
    d.add_paragraph("한글 본문 문단입니다.")
    src = tmp_path / "a.docx"
    d.save(src)
    doc = default(str(src), converter="local", backend=FakeBackend())
    assert doc.meta["convert"]["converter"] == "libreoffice"
    assert "한글 본문 문단입니다." in " ".join(b.text for _, b in doc.iter_blocks())
    validate_doc(doc)


def test_unreachable_server_is_a_convert_error(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(office_zip("word/document.xml"))
    with pytest.raises(ConvertError, match="convert server http://127.0.0.1:9: ConnectError"):
        default(str(src), converter={"url": "http://127.0.0.1:9", "timeout": 2})
