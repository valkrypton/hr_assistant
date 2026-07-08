"""Permission policy layer.

Central place to ask "may this role do X?" instead of scattering
`if role == "hr_manager"` checks across the codebase.
"""

from core.policies.evaluator import can, scope_for

__all__ = ["can", "scope_for"]
