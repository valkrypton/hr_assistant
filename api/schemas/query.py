from typing import Optional

from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str
    slack_user_id: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
