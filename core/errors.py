"""Typed domain errors raised by the core layer.

The API layer maps these to HTTP status codes; the Slack adapter maps them to
friendly in-thread messages. Keeping them here (not as HTTPException) preserves
the rule that core/ never depends on the web framework.
"""


class HRAgentError(Exception):
    """Base class for expected, user-facing core errors."""


class UserNotRegistered(HRAgentError):
    def __init__(
        self,
        message: str = "User not registered. Ask your HR admin to add your Slack account.",
    ) -> None:
        super().__init__(message)


class MissingRole(HRAgentError):
    def __init__(
        self,
        message: str = "Your account has no role assigned. Ask your HR admin to set your role.",
    ) -> None:
        super().__init__(message)


class RateLimitExceeded(HRAgentError):
    def __init__(self, message: str = "Rate limit exceeded. Try again later.") -> None:
        super().__init__(message)


class PermissionDenied(HRAgentError):
    def __init__(self, message: str = "You are not authorized to perform this action.") -> None:
        super().__init__(message)
