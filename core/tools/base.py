"""Tool specification + authorized execution.

A Tool bundles the metadata an agent runtime needs to expose it (name,
description, typed input) with the permissions required to call it. Authorization
is enforced here — in the tool layer — so it holds regardless of what the model
was told in its prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from core.policies import can

if TYPE_CHECKING:
    from core.identity.context import AgentContext


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    required_permissions: tuple[str, ...]
    fn: Callable[..., str]

    def authorized(self, ctx: AgentContext | None) -> bool:
        """Whether this context may call the tool.

        A None context is the unscoped, already-authenticated admin/open path
        (matching how the SQL guard treats rbac_ctx=None as unrestricted, with
        the guard itself as the backstop). A scoped context must hold every
        required permission.
        """
        if ctx is None:
            return True
        return all(can(ctx, perm) for perm in self.required_permissions)

    def execute(self, ctx: AgentContext | None, **kwargs) -> str:
        """Validate args, enforce permissions, run. Returns a string observation.

        Permission denial and input-validation errors are returned as an
        "Error: ..." observation (not raised) so a tool-calling agent can react
        and retry, mirroring the SQL guard's contract.
        """
        if not self.authorized(ctx):
            perms = list(self.required_permissions)
            return f"Error: permission denied — this tool requires {perms}."
        try:
            args = self.input_model(**kwargs)
        except Exception as exc:
            return f"Error: invalid input for tool '{self.name}': {exc}"
        return self.fn(ctx, **args.model_dump())
