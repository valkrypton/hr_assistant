"""
HR Assistant — API layer (FastAPI).

The API layer depends on core, but core must never depend on the API layer.

Two databases:
  DATABASE_URL     — ERP (read-only). Only the SQL agent touches this.
  APP_DATABASE_URL — App DB (writable). Owns hr_assistant_users, audit logs, etc.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import sqlalchemy
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqladmin import Admin

from core.config import settings
from core.agent import get_agent, query
from core.rbac.models import Base, HRUser, AuditLog
from core.rbac.roles import Role
from core.rbac.context import RBACContext
from api.admin import HRUserAdmin, AuditLogAdmin


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _app_engine():
    """Writable engine for our own tables (hr_assistant_users, etc.)."""
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


def _erp_engine():
    """Read-only ERP engine — used only for the health check."""
    return sqlalchemy.create_engine(settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(_app_engine())
    get_agent()  # warm up the shared unrestricted agent on startup
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HR Assistant API",
    description="Natural-language interface to company HR/ERP data.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SQLAdmin panel  →  http://localhost:8000/admin
# ---------------------------------------------------------------------------

admin = Admin(app, engine=_app_engine())
admin.add_view(HRUserAdmin)
admin.add_view(AuditLogAdmin)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    slack_user_id: Optional[str] = None  # when set, RBAC scope is enforced


class QueryResponse(BaseModel):
    answer: str


class UserCreate(BaseModel):
    employee_id: int
    role: Role
    slack_user_id: str
    department_id: Optional[int] = None
    team_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    employee_id: int
    role: Role
    slack_user_id: Optional[str]
    department_id: Optional[int]
    team_id: Optional[int]
    is_active: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    results = {}
    # Check ERP DB
    try:
        with _erp_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["erp_database"] = "connected"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ERP database unreachable: {exc}") from exc
    # Check app DB
    try:
        with _app_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        results["app_database"] = "connected"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"App database unreachable: {exc}") from exc
    return {"status": "ok", **results}


def _write_audit(
    *,
    slack_user_id: Optional[str],
    employee_id: Optional[int],
    role: Optional[str],
    question: str,
    answer: Optional[str] = None,
    tables_accessed: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Append one row to the audit log in the app DB (FR-6.1 / FR-6.2)."""
    with Session(_app_engine()) as session:
        session.add(AuditLog(
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=question,
            answer=answer,
            tables_accessed=tables_accessed,
            error=error,
        ))
        session.commit()


@app.post("/query", response_model=QueryResponse)
def query_endpoint(request: QueryRequest):
    """Accept a natural-language HR question and return an answer from the ERP."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None
    hr_user = None
    if request.slack_user_id:
        with Session(_app_engine()) as session:
            hr_user = (
                session.query(HRUser)
                .filter_by(slack_user_id=request.slack_user_id, is_active=True)
                .first()
            )
        if not hr_user:
            raise HTTPException(status_code=403, detail="Slack user not registered.")
        rbac_ctx = RBACContext.for_user(hr_user)

    try:
        answer, tables_accessed = query(request.query, rbac_ctx=rbac_ctx)
        _write_audit(
            slack_user_id=request.slack_user_id,
            employee_id=hr_user.employee_id if hr_user else None,
            role=hr_user.role if hr_user else None,
            question=request.query,
            answer=answer,
            tables_accessed=tables_accessed or None,
        )
        return QueryResponse(answer=answer)
    except Exception as exc:
        _write_audit(
            slack_user_id=request.slack_user_id,
            employee_id=hr_user.employee_id if hr_user else None,
            role=hr_user.role if hr_user else None,
            question=request.query,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Admin REST API — user registration (FR-7.1)
# (The /admin panel above is the preferred UI; these endpoints exist for
#  scripting and integration testing.)
# ---------------------------------------------------------------------------

@app.get("/admin/users", response_model=list[UserResponse])
def list_users():
    """List all active HR agent users."""
    with Session(_app_engine()) as session:
        return [
            UserResponse(
                id=u.id,
                employee_id=u.employee_id,
                role=u.role,
                slack_user_id=u.slack_user_id,
                department_id=u.department_id,
                team_id=u.team_id,
                is_active=u.is_active,
            )
            for u in session.query(HRUser).filter_by(is_active=True).all()
        ]


@app.post("/admin/users", response_model=UserResponse, status_code=201)
def register_user(body: UserCreate):
    """Register an employee as an HR agent user with a given role."""
    with Session(_app_engine()) as session:
        user = HRUser(
            employee_id=body.employee_id,
            role=body.role.value,
            slack_user_id=body.slack_user_id,
            department_id=body.department_id,
            team_id=body.team_id,
        )
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UserResponse(
            id=user.id,
            employee_id=user.employee_id,
            role=user.role,
            slack_user_id=user.slack_user_id,
            department_id=user.department_id,
            team_id=user.team_id,
            is_active=user.is_active,
        )


@app.delete("/admin/users/{user_id}", status_code=204)
def deregister_user(user_id: int):
    """Deactivate a user (soft delete)."""
    with Session(_app_engine()) as session:
        user = session.get(HRUser, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        user.is_active = False
        session.commit()


# ---------------------------------------------------------------------------
# Audit log  (FR-6.3)
# ---------------------------------------------------------------------------

class AuditLogResponse(BaseModel):
    id: int
    created_at: str
    slack_user_id: Optional[str]
    employee_id: Optional[int]
    role: Optional[str]
    question: str
    answer: Optional[str]
    tables_accessed: Optional[str]
    error: Optional[str]


@app.get("/audit", response_model=list[AuditLogResponse])
def get_audit_logs(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 100,
):
    """
    Retrieve audit log entries. Supports filtering by date range, user, and role.

    - from_date / to_date: ISO-8601 date strings (e.g. 2025-01-01)
    - slack_user_id: filter to a specific Slack user
    - role: filter to a specific role (cto_ceo, hr_manager, dept_head, team_lead)
    - limit: max rows to return (default 100, max 1000)
    """
    from datetime import date
    limit = min(limit, 1000)

    with Session(_app_engine()) as session:
        q = session.query(AuditLog)
        if from_date:
            q = q.filter(AuditLog.created_at >= from_date)
        if to_date:
            q = q.filter(AuditLog.created_at <= to_date)
        if slack_user_id:
            q = q.filter(AuditLog.slack_user_id == slack_user_id)
        if role:
            q = q.filter(AuditLog.role == role)
        rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()

    return [
        AuditLogResponse(
            id=r.id,
            created_at=r.created_at.isoformat(),
            slack_user_id=r.slack_user_id,
            employee_id=r.employee_id,
            role=r.role,
            question=r.question,
            answer=r.answer,
            tables_accessed=r.tables_accessed,
            error=r.error,
        )
        for r in rows
    ]


if __name__ == "__main__":
    config = uvicorn.Config("api.main:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
