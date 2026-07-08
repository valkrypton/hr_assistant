import sqlalchemy
import structlog
from fastapi import APIRouter, HTTPException

from api.deps import app_engine, erp_engine

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/health")
def health():
    results = {}
    try:
        with erp_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["erp_database"] = "connected"
    except Exception as exc:
        logger.error("erp_health_check_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="ERP database unreachable.") from exc
    try:
        with app_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["app_database"] = "connected"
    except Exception as exc:
        logger.error("app_health_check_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="App database unreachable.") from exc
    return {"status": "ok", **results}
