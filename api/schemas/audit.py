from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: int
    created_at: str
    slack_user_id: str | None
    employee_id: int | None
    role: str | None
    question: str
    answer: str | None
    tables_accessed: str | None
    error: str | None
    schema_rag_ms: int | None
    agent_ms: int | None
    total_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
