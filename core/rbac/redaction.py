"""Forbidden-column redaction — a global data-protection rule independent of any
requester's scope (SRP: this has nothing to do with which department/team a user
can see). Owns FORBIDDEN_COLUMNS so scope logic and redaction no longer share a
module."""

from __future__ import annotations

import re

# Columns that must never appear in any agent response, regardless of role.
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "salary",
        "basic_salary",
        "gross_salary",
        "net_salary",
        "compensation",
        "nic",
        "cnic",
        "bank_account",
        "bank_details",
        "home_address",
        "personal_address",
        "personal_phone",
        "personal_email",
        "date_of_birth",
        "dob",
        "passport_number",
        "medical_record",
    }
)


class ForbiddenColumnRedactor:
    """Best-effort scan of the agent's text output for forbidden column names.
    Defence-in-depth — the primary enforcement is the prompt; this catches cases
    where the LLM ignores the instruction."""

    def __init__(self, forbidden: frozenset[str] = FORBIDDEN_COLUMNS) -> None:
        self._forbidden = forbidden

    def strip(self, text: str) -> str:
        lower = text.lower()
        # Word boundaries avoid partial matches (e.g. "nic" inside "cnic").
        found = [col for col in self._forbidden if re.search(rf"\b{re.escape(col)}\b", lower)]
        if not found:
            return text

        # Process longer keys first to avoid partial-overlap redactions.
        sanitised = text
        for col in sorted(found, key=len, reverse=True):
            sanitised = re.sub(
                rf"(?i)\b{re.escape(col)}\b\s*[:\-=]?\s*[^\n,;]+",
                f"[{col.upper()} REDACTED]",
                sanitised,
            )
        return sanitised
