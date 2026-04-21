import logging

import sqlalchemy
from fastapi import APIRouter, HTTPException

from api.deps import app_engine, erp_engine

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health():
    results = {}
    try:
        with erp_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["erp_database"] = "connected"
    except Exception as exc:
        logger.error("ERP database health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="ERP database unreachable.") from exc
    try:
        with app_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["app_database"] = "connected"
    except Exception as exc:
        logger.error("App database health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="App database unreachable.") from exc
    return {"status": "ok", **results}
