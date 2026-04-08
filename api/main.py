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
from core.rbac.models import Base, HRUser
from core.rbac.roles import Role
from core.rbac.context import RBACContext
from api.admin import HRUserAdmin


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


@app.post("/query", response_model=QueryResponse)
def query_endpoint(request: QueryRequest):
    """Accept a natural-language HR question and return an answer from the ERP."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    rbac_ctx = None
    if request.slack_user_id:
        with Session(_app_engine()) as session:
            user = (
                session.query(HRUser)
                .filter_by(slack_user_id=request.slack_user_id, is_active=True)
                .first()
            )
        if not user:
            raise HTTPException(status_code=403, detail="Slack user not registered.")
        rbac_ctx = RBACContext.for_user(user)

    try:
        answer = query(request.query, rbac_ctx=rbac_ctx)
        return QueryResponse(answer=answer)
    except Exception as exc:
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


if __name__ == "__main__":
    config = uvicorn.Config("api.main:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
