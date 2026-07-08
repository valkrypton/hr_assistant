"""Permission policy layer.

Central place to ask "may this role do X?" instead of scattering
`if role == "hr_manager"` checks across the codebase.
"""

from core.policies.evaluator import can, permissions_for, scope_for

__all__ = ["can", "permissions_for", "scope_for"]
