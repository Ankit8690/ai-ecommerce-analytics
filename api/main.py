"""
FastAPI Main Application Entry Point.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import check_db_health
from api.routes import analytics, analyst, ml
from api.schemas import HealthResponse

app = FastAPI(
    title="E-Commerce AI Analytics API",
    description="Production-grade API exposing PostgreSQL analytics views, ML models, and AI Business Analyst.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for frontend/dashboard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(analytics.router)
app.include_router(ml.router)
app.include_router(analyst.router)


@app.get("/health", response_model=HealthResponse, tags=["Health"], summary="Health Check")
def health_check() -> HealthResponse:
    """Check API operational status and read-only database connectivity."""
    db_ok = check_db_health()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database_connected=db_ok,
        version="1.0.0"
    )


@app.get("/", tags=["Health"], summary="Root Endpoint")
def root():
    return {
        "name": "E-Commerce AI Analytics API",
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }
