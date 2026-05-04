"""LLM-based document sanitization with chunked processing for long sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AzureOpenAI, OpenAI

from uab_app.constants import (
    DOCUMENT_CLEAN_CHUNK_OVERLAP,
    DOCUMENT_CLEAN_MAX_CHUNK_CHARS,
)
from uab_app.sanitize import regex_cleanup_fallback, strip_control_chars


@dataclass
class DocumentCleanupResult:
    """Result of cleaning uploaded document text for the infographic prompt."""

    text: str
    original_chars: int
    chunks_processed: int
    truncated_input: bool


def _chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def clean_document_text_llm(
    client: OpenAI | AzureOpenAI,
    provider: str,
    chat_model: str,
    text: str,
) -> DocumentCleanupResult:
    """Sanitize via chat model; long texts are cleaned in overlapping chunks and concatenated."""
    original_chars = len(text)
    chunks_in = _chunk_text(
        text,
        DOCUMENT_CLEAN_MAX_CHUNK_CHARS,
        DOCUMENT_CLEAN_CHUNK_OVERLAP,
    )
    truncated_input = len(chunks_in) > 1

    sys_msg = (
        "You sanitize text for UAB Medicine infographic source context. "
        "Remove control characters and odd whitespace; normalize common OCR artifacts; "
        "redact or remove PHI-adjacent lines (patient names, DOB, MRN, phone numbers, addresses). "
        "Preserve headings, bullets, structure, and factual medical content. "
        "Output ONLY the cleaned text, no preamble."
    )

    cleaned_parts: list[str] = []
    for chunk in chunks_in:
        try:
            kwargs: dict[str, Any] = {
                "model": chat_model,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": chunk},
                ],
                "temperature": 0.2,
                "max_tokens": 4096,
            }
            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0].message.content
            if choice and choice.strip():
                cleaned_parts.append(strip_control_chars(choice.strip()))
        except Exception:
            cleaned_parts.append(regex_cleanup_fallback(chunk))

    if not cleaned_parts:
        return DocumentCleanupResult(
            text=regex_cleanup_fallback(text),
            original_chars=original_chars,
            chunks_processed=len(chunks_in),
            truncated_input=truncated_input,
        )

    merged = "\n\n".join(cleaned_parts)
    return DocumentCleanupResult(
        text=strip_control_chars(merged),
        original_chars=original_chars,
        chunks_processed=len(chunks_in),
        truncated_input=truncated_input,
    )


def cache_key_for_raw(name: str, raw_text: str) -> str:
    """Stable key for session-scoped cleaned-document cache."""
    import hashlib

    h = hashlib.sha256(raw_text.encode("utf-8", errors="replace")).hexdigest()
    return f"{name}|{len(raw_text)}|{h}"
