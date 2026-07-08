from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str
    slack_user_id: str | None = None


class QueryResponse(BaseModel):
    answer: str
