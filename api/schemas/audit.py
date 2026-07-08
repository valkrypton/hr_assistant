from typing import Optional

from pydantic import BaseModel


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
    schema_rag_ms: Optional[int]
    agent_ms: Optional[int]
    total_ms: Optional[int]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
