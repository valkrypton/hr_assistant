from typing import Optional

from fastapi import APIRouter, Depends

from api.deps import require_admin_unless_open
from api.schemas.query import QueryRequest, QueryResponse
from api.services.query_service import run_query as run_query_service
from core.rbac.models import AdminUser

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def run_query(
    body: QueryRequest,
    admin: Optional[AdminUser] = Depends(require_admin_unless_open),
):
    """
    Natural-language HR query endpoint.

    Requires admin HTTP Basic auth unless ALLOW_UNAUTHENTICATED_QUERY=true
    (local dev). slack_user_id selects the RBAC scope to apply — it is not an
    identity proof; end-user traffic goes through the signed Slack webhook.

    - No slack_user_id: runs without RBAC. Allowed only for an authenticated
      admin or when ALLOW_UNAUTHENTICATED_QUERY=true — require_admin_unless_open
      has already enforced this, so no further auth check is needed here.
    - With slack_user_id: enforces RBAC based on the user's registered role.
    """
    return run_query_service(body)
