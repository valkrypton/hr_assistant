from fastapi import APIRouter, Depends

from api.deps import require_admin
from api.schemas.query import QueryRequest, QueryResponse
from api.services.query_service import run_query as run_query_service
from core.rbac.models import AdminUser

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def run_query(
    body: QueryRequest,
    admin: AdminUser = Depends(require_admin),
):
    """
    Natural-language HR query endpoint.

    Always requires admin HTTP Basic auth. slack_user_id selects the RBAC
    scope to apply — it is not an identity proof; end-user traffic goes
    through the signed Slack webhook.

    - No slack_user_id: runs without RBAC — the admin credentials are the
      identity proof, no further auth check is needed here.
    - With slack_user_id: enforces RBAC based on the user's registered role.
    """
    return run_query_service(body)
