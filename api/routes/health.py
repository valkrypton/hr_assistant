import sqlalchemy
from fastapi import APIRouter, HTTPException

from api.deps import app_engine, erp_engine

router = APIRouter()


@router.get("/health")
def health():
    results = {}
    try:
        with erp_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["erp_database"] = "connected"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ERP database unreachable: {exc}") from exc
    try:
        with app_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["app_database"] = "connected"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"App database unreachable: {exc}") from exc
    return {"status": "ok", **results}
