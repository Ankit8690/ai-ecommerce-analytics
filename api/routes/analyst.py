"""
AI Business Analyst API Router.
Exposes POST /api/analyst endpoint for natural-language e-commerce business questions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ai.analyst_engine import process_analyst_question
from api.database import readonly_engine
from api.schemas import AnalystRequest, AnalystResponse

router = APIRouter(prefix="/api", tags=["AI Analyst"])


@router.post("/analyst", response_model=AnalystResponse, summary="Ask AI Business Analyst")
def ask_ai_analyst(payload: AnalystRequest) -> AnalystResponse:
    """
    Process a natural language business question.
    Routes to PostgreSQL analytics views / ML outputs, executes safe read-only queries,
    and returns a grounded business answer with structured data.
    """
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question text cannot be empty")
        
    try:
        result = process_analyst_question(readonly_engine, payload.question.strip())
        return AnalystResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process analyst request: {str(e)}")
