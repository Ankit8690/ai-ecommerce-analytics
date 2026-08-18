"""
Grounded answer synthesis for RAG queries.

Preferred path: Gemini receives the question + retrieved chunks and produces a
concise answer that cites which chunks it used.

Fallback (used when LLM_API_KEY is absent or Gemini fails/quota-exhausts):
a deterministic answer that surfaces the top retrieved chunks verbatim with
citations, so the user still gets grounded knowledge — never fabricated.
"""
from __future__ import annotations

import concurrent.futures
import os
from typing import Optional

from rag.retriever import RetrievalResult

_DEFAULT_MODEL = "gemini-3.6-flash"

_SYSTEM_PROMPT = (
    "You are the knowledge assistant for an e-commerce analytics platform. "
    "Answer the user's question using ONLY the numbered context excerpts. "
    "Never invent facts, numbers, tables, or definitions that are not in the excerpts. "
    "Cite the excerpts you used inline with [1], [2], etc. "
    "If the excerpts do not contain the answer, say so explicitly. "
    "Keep the answer concise (under 180 words) and use GitHub Markdown."
)


def _format_context(results: list[RetrievalResult]) -> str:
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] Source: {r.citation()}  (relevance {r.score:.2f})\n"
            f"{r.chunk.text.strip()}"
        )
    return "\n\n---\n\n".join(parts)


def _try_gemini(question: str, results: list[RetrievalResult],
                timeout_s: int = 40) -> Optional[str]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("LLM_MODEL") or _DEFAULT_MODEL
    try:
        from google import genai
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None  # type: ignore

        client = genai.Client(api_key=api_key)
        prompt = (
            f'Question: "{question}"\n\n'
            f"Context excerpts:\n{_format_context(results)}\n\n"
            "Write the grounded answer now."
        )

        def _gen():
            if types is not None:
                try:
                    cfg = types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        temperature=0.2,
                        max_output_tokens=500,
                    )
                    return client.models.generate_content(
                        model=model, contents=prompt, config=cfg)
                except Exception:
                    pass
            return client.models.generate_content(
                model=model, contents=f"{_SYSTEM_PROMPT}\n\n{prompt}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            resp = ex.submit(_gen).result(timeout=timeout_s)
        return getattr(resp, "text", None) or None
    except Exception:
        return None


def _deterministic_answer(question: str, results: list[RetrievalResult]) -> str:
    """Grounded fallback — surface citations without inventing prose."""
    if not results:
        return (
            f"### No knowledge-base results\n\n"
            f"I couldn't find relevant excerpts in the project documentation for: "
            f"*\"{question}\"*.\n\n"
            f"Try rephrasing, or ask the AI Analyst if this is a numerical question "
            f"answerable from the analytics database."
        )
    lines = [
        f"### Answer (retrieved excerpts, no LLM synthesis)",
        f"*Gemini synthesis unavailable — showing the top {len(results)} grounded excerpts.*",
        "",
    ]
    for i, r in enumerate(results, 1):
        # Trim excerpt to keep the panel readable
        excerpt = r.chunk.text.strip()
        if len(excerpt) > 700:
            excerpt = excerpt[:680].rsplit(" ", 1)[0] + "…"
        lines.append(f"**[{i}] {r.citation()}** — relevance {r.score:.2f}")
        lines.append("")
        lines.append(excerpt)
        lines.append("")
    return "\n".join(lines)


def answer_from_context(question: str,
                        results: list[RetrievalResult]) -> tuple[str, str]:
    """
    Produce a grounded answer given retrieved chunks.

    Returns (answer_markdown, mode) where mode ∈ {"gemini", "fallback", "no_results"}.
    """
    if not results:
        return _deterministic_answer(question, results), "no_results"

    text = _try_gemini(question, results)
    if text:
        # Append a compact source footer regardless of whether the LLM cited them
        footer = "\n\n---\n**Sources**\n" + "\n".join(
            f"- [{i}] {r.citation()} (relevance {r.score:.2f})"
            for i, r in enumerate(results, 1)
        )
        return text.rstrip() + footer, "gemini"
    return _deterministic_answer(question, results), "fallback"
