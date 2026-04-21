from enum import Enum


class Role(str, Enum):
    """
    Four roles from SPEC.md FR-5.2.

    Scope rules:
      CTO_CEO       — full company-wide access
      HR_MANAGER    — company-wide (employee details, leaves, attrition, utilisation)
      DEPT_HEAD     — own department only
      TEAM_LEAD     — own team only (member names, skills, availability, projects)
    """
    CTO_CEO = "cto_ceo"
    HR_MANAGER = "hr_manager"
    DEPT_HEAD = "dept_head"
    TEAM_LEAD = "team_lead"
