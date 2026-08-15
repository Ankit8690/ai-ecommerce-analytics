"""
AI Business Analyst API Router.
Exposes POST /api/analyst endpoint for natural-language e-commerce business questions.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ai.analyst_engine import process_analyst_question
from api.database import readonly_engine
from api.schemas import AnalystRequest, AnalystResponse

router = APIRouter(prefix="/api", tags=["AI Analyst"])
logger = logging.getLogger(__name__)

# Cap free-form input length defensively (Pydantic min_length=2 already blocks empty)
_MAX_QUESTION_LEN = 1000


@router.post("/analyst", response_model=AnalystResponse, summary="Ask AI Business Analyst")
def ask_ai_analyst(payload: AnalystRequest) -> AnalystResponse:
    """
    Process a natural language business question.
    Routes to PostgreSQL analytics views / ML outputs, executes safe read-only queries,
    and returns a grounded business answer with structured data.
    """
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question text cannot be empty")
    if len(q) > _MAX_QUESTION_LEN:
        raise HTTPException(status_code=413,
                            detail=f"Question exceeds maximum length ({_MAX_QUESTION_LEN} chars)")
    try:
        result = process_analyst_question(readonly_engine, q)
        return AnalystResponse(**result)
    except Exception:
        # Log full exception server-side; return a generic message to the client so
        # DB connection strings / driver internals are never surfaced to a caller.
        logger.exception("Analyst request failed")
        raise HTTPException(status_code=500,
                            detail="Analyst request failed. See server logs for details.")
