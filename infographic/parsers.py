"""Document text extraction (PDF, DOCX, TXT)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import docx
import PyPDF2

from infographic.constants import ALLOWED_UPLOAD_EXTENSIONS


def read_pdf(file: io.BytesIO) -> str:
    try:
        reader = PyPDF2.PdfReader(file)
        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n\n".join(parts)
    except Exception:
        return ""


def read_docx_bytes(file: io.BytesIO) -> str:
    try:
        document = docx.Document(file)
        paras = [p.text for p in document.paragraphs if p.text.strip()]
        return "\n\n".join(paras)
    except Exception:
        return ""


def read_txt_bytes(file: io.BytesIO) -> str:
    try:
        return file.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_document_text(uploaded_file: Any) -> str:
    fname = uploaded_file.name.lower()
    ext = Path(fname).suffix
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return ""
    raw = uploaded_file.getvalue()
    file_bytes = io.BytesIO(raw)
    if ext == ".pdf":
        return read_pdf(file_bytes)
    if ext == ".docx":
        return read_docx_bytes(file_bytes)
    if ext == ".txt":
        return read_txt_bytes(file_bytes)
    return ""
