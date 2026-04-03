"""
HR Assistant — API layer (FastAPI).

The API layer depends on core, but core must never depend on the API layer.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.agent import query as core_query

app = FastAPI(
    title="HR Assistant API",
    description="Natural-language interface to company HR/ERP data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    answer: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query_endpoint(request: QueryRequest):
    """
    Accept a natural-language question about employees / HR data and return
    an answer derived from the company database.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    try:
        answer = core_query(request.query)
        return QueryResponse(answer=answer)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
