"""Hardened document parser. Runs with no network egress and no API keys."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from charset_normalizer import from_bytes
from fastapi import FastAPI, File, UploadFile

MAX_BYTES = 512 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".rtf", ".csv", ".json", ".log", ".text", ""}


def _parse_pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), len(pages)


def _parse_docx(data: bytes) -> tuple[str, int]:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts), 1


def _parse_text(data: bytes) -> tuple[str, int]:
    best = from_bytes(data).best()
    return (str(best) if best else data.decode("utf-8", errors="replace")), 1


def _parse_ocr(data: bytes, suffix: str) -> tuple[str, int]:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract is not installed in this image")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix or '.pdf'}"
        src.write_bytes(data)
        if suffix.lower() == ".pdf":
            if not shutil.which("pdftoppm"):
                raise RuntimeError("pdftoppm is not installed in this image")
            subprocess.run(["pdftoppm", "-r", "200", "-png", str(src), f"{tmp}/page"],
                           check=True, timeout=600, capture_output=True)
            images = sorted(Path(tmp).glob("page*.png"))
        else:
            images = [src]
        chunks = []
        for image in images:
            proc = subprocess.run(["tesseract", str(image), "stdout"],
                                  check=True, timeout=600, capture_output=True)
            chunks.append(proc.stdout.decode("utf-8", errors="replace"))
        return "\n".join(chunks), len(images)


def create_app() -> FastAPI:
    app = FastAPI(title="Parser Sandbox", version="1.0.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/parse")
    async def parse(file: UploadFile = File(...), ocr: bool = False) -> dict:
        data = await file.read()
        if len(data) > MAX_BYTES:
            return {"text": "", "pages": 0, "parser": "none", "ok": False,
                    "reason": "file exceeds size limit"}

        suffix = Path(file.filename or "").suffix.lower()
        try:
            if ocr:
                text, pages = _parse_ocr(data, suffix)
                parser = "ocr"
            elif suffix == ".pdf":
                text, pages = _parse_pdf(data)
                parser = "pypdf"
            elif suffix in {".docx", ".doc"}:
                text, pages = _parse_docx(data)
                parser = "python-docx"
            elif suffix in TEXT_SUFFIXES:
                text, pages = _parse_text(data)
                parser = "text"
            else:
                return {"text": "", "pages": 0, "parser": "none", "ok": False,
                        "reason": f"unsupported extension {suffix!r}"}
        except Exception as exc:  # noqa: BLE001 - the sandbox reports, never raises
            return {"text": "", "pages": 0, "parser": "none", "ok": False,
                    "reason": f"{type(exc).__name__}: {exc}"}

        if not text.strip():
            return {"text": "", "pages": pages, "parser": parser, "ok": False,
                    "reason": "no extractable text"}
        return {"text": text, "pages": pages, "parser": parser, "ok": True, "reason": None}

    return app


app = create_app()
