"""LLM-based document sanitization with chunked processing for long sources."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
import json
import re

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


@dataclass
class SourceProfileResult:
    """Structured source profile inferred from docs + user context."""

    topic: str
    objective: str
    source_type: str
    why_matters: str
    key_points: list[str]
    recommended_sections: list[str]
    chart_guidance: str
    citation_title: str
    citation_journal: str
    citation_year: str
    citation_authors_short: str
    citation_footer: str
    implications_panel: str
    claim_evidence_pairs: list[str]
    non_numeric_mode: bool


def _safe_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {}
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def infer_source_profile_llm(
    client: OpenAI | AzureOpenAI,
    provider: str,
    chat_model: str,
    user_context: str,
    cleaned_document_texts: list[str],
    audience: str,
) -> SourceProfileResult:
    """Infer missing prompt elements (topic/objective/why-matters/structure)."""
    docs_joined = "\n\n".join([d for d in cleaned_document_texts if d.strip()])
    docs_snippet = docs_joined[:12000] if docs_joined else ""
    user_ctx = (user_context or "").strip()

    fallback_topic = "the attached source material"
    fallback_objective = (
        f"Create a {audience}-appropriate infographic summarizing the key findings from the attached source."
    )
    fallback = SourceProfileResult(
        topic=fallback_topic,
        objective=fallback_objective,
        source_type="unknown",
        why_matters="Explain practical relevance for the target audience.",
        key_points=["Summarize key findings accurately from source text."],
        recommended_sections=["Background", "Key Findings", "Why This Matters", "Actions/Implications"],
        chart_guidance="Include charts only when explicit numeric data is present in the source.",
        citation_title="",
        citation_journal="",
        citation_year="",
        citation_authors_short="",
        citation_footer="Source: [Title]. [Journal], [Year].",
        implications_panel="What this means for practice/research: summarize practical impact in 2-3 short bullets.",
        claim_evidence_pairs=[
            "Claim: [fill from source]. Evidence: [verbatim source phrase or statistic]."
        ],
        non_numeric_mode=False,
    )

    nums = re.findall(r"\b\d+(?:\.\d+)?\b", docs_snippet)
    has_effect_tokens = bool(
        re.search(r"\b(hr|hazard ratio|odds ratio|relative risk|95% ci|p<|p\s*=)\b", docs_snippet, flags=re.I)
    )
    heuristic_non_numeric = (len(nums) < 12) and (not has_effect_tokens)

    sys_msg = (
        "You analyze source text and fill missing infographic planning fields. "
        "Return ONLY valid JSON with keys: "
        "topic, objective, source_type, why_matters, key_points, recommended_sections, chart_guidance, "
        "citation_title, citation_journal, citation_year, citation_authors_short, citation_footer, "
        "implications_panel, claim_evidence_pairs, non_numeric_mode. "
        "Use concise, source-grounded wording. Do not invent facts."
    )
    user_msg = (
        f"Audience: {audience}\n"
        f"User context (may be empty):\n{user_ctx or '[none]'}\n\n"
        f"Source text excerpt:\n{docs_snippet or '[none]'}"
    )

    try:
        resp = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=900,
        )
        raw = (resp.choices[0].message.content or "").strip()
        obj = _safe_json_object(raw)
        if not obj:
            return fallback

        topic = str(obj.get("topic") or fallback.topic).strip()
        objective = str(obj.get("objective") or fallback.objective).strip()
        source_type = str(obj.get("source_type") or fallback.source_type).strip()
        why_matters = str(obj.get("why_matters") or fallback.why_matters).strip()
        key_points_raw = obj.get("key_points")
        sections_raw = obj.get("recommended_sections")
        key_points = (
            [str(x).strip() for x in key_points_raw if str(x).strip()]
            if isinstance(key_points_raw, list)
            else fallback.key_points
        )
        recommended_sections = (
            [str(x).strip() for x in sections_raw if str(x).strip()]
            if isinstance(sections_raw, list)
            else fallback.recommended_sections
        )
        chart_guidance = str(obj.get("chart_guidance") or fallback.chart_guidance).strip()
        claim_pairs_raw = obj.get("claim_evidence_pairs")
        claim_pairs = (
            [str(x).strip() for x in claim_pairs_raw if str(x).strip()]
            if isinstance(claim_pairs_raw, list)
            else fallback.claim_evidence_pairs
        )
        non_numeric_mode = obj.get("non_numeric_mode")
        if isinstance(non_numeric_mode, bool):
            inferred_non_numeric = non_numeric_mode
        else:
            inferred_non_numeric = heuristic_non_numeric

        if inferred_non_numeric:
            chart_guidance = (
                "NON_NUMERIC_MODE: Prefer concept maps/framework diagrams/process flows. "
                "Do not fabricate quantitative charts, effect sizes, CI values, or pseudo-statistics."
            )

        return SourceProfileResult(
            topic=topic or fallback.topic,
            objective=objective or fallback.objective,
            source_type=source_type or fallback.source_type,
            why_matters=why_matters or fallback.why_matters,
            key_points=key_points or fallback.key_points,
            recommended_sections=recommended_sections or fallback.recommended_sections,
            chart_guidance=chart_guidance or fallback.chart_guidance,
            citation_title=str(obj.get("citation_title") or "").strip(),
            citation_journal=str(obj.get("citation_journal") or "").strip(),
            citation_year=str(obj.get("citation_year") or "").strip(),
            citation_authors_short=str(obj.get("citation_authors_short") or "").strip(),
            citation_footer=str(obj.get("citation_footer") or fallback.citation_footer).strip(),
            implications_panel=str(obj.get("implications_panel") or fallback.implications_panel).strip(),
            claim_evidence_pairs=claim_pairs or fallback.claim_evidence_pairs,
            non_numeric_mode=inferred_non_numeric,
        )
    except Exception:
        if heuristic_non_numeric:
            fallback.chart_guidance = (
                "NON_NUMERIC_MODE: Prefer concept maps/framework diagrams/process flows. "
                "Do not fabricate quantitative charts, effect sizes, CI values, or pseudo-statistics."
            )
            fallback.non_numeric_mode = True
        return fallback


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

    def _clean_chunk(idx: int, chunk: str) -> tuple[int, str]:
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
                return idx, strip_control_chars(choice.strip())
            return idx, regex_cleanup_fallback(chunk)
        except Exception:
            return idx, regex_cleanup_fallback(chunk)

    # For long documents, clean chunks concurrently to reduce wall-clock time.
    # Keep worker count bounded to avoid API throttling.
    max_workers_env = os.environ.get("DOCUMENT_CLEAN_MAX_WORKERS", "").strip()
    try:
        max_workers = int(max_workers_env) if max_workers_env else 4
    except ValueError:
        max_workers = 4
    max_workers = max(1, min(max_workers, 8))

    cleaned_by_idx: dict[int, str] = {}
    if len(chunks_in) == 1:
        idx, text_out = _clean_chunk(0, chunks_in[0])
        cleaned_by_idx[idx] = text_out
    else:
        worker_count = min(max_workers, len(chunks_in))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futs = [pool.submit(_clean_chunk, i, chunk) for i, chunk in enumerate(chunks_in)]
            for fut in as_completed(futs):
                idx, text_out = fut.result()
                cleaned_by_idx[idx] = text_out

    cleaned_parts = [cleaned_by_idx[i] for i in sorted(cleaned_by_idx.keys()) if cleaned_by_idx[i].strip()]

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
