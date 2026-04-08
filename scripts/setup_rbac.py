"""
Create (or verify) the hr_assistant_users table in the configured database.

Usage:
    python scripts/setup_rbac.py              # create table if it doesn't exist
    python scripts/setup_rbac.py --seed       # also insert example rows for dev
"""
import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect

from core.config import settings
from core.rbac.models import Base, HRUser
from core.rbac.roles import Role


def create_tables(engine) -> None:
    Base.metadata.create_all(engine)
    print("hr_assistant_users table ready.")


def seed(engine) -> None:
    """Insert example rows so local dev/testing works without real ERP data."""
    from sqlalchemy.orm import Session

    examples = [
        HRUser(employee_id=1, role=Role.CTO_CEO, slack_user_id="U_CTO_EXAMPLE"),
        HRUser(employee_id=2, role=Role.HR_MANAGER, slack_user_id="U_HR_EXAMPLE"),
        HRUser(employee_id=3, role=Role.DEPT_HEAD, slack_user_id="U_DH_EXAMPLE", department_id=1),
        HRUser(employee_id=4, role=Role.TEAM_LEAD, slack_user_id="U_TL_EXAMPLE", team_id=1),
    ]

    with Session(engine) as session:
        for user in examples:
            exists = (
                session.query(HRUser)
                .filter_by(employee_id=user.employee_id)
                .first()
            )
            if not exists:
                session.add(user)
        session.commit()

    print(f"Seeded {len(examples)} example users (skipped existing).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up RBAC tables.")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Insert example dev rows after creating the table.",
    )
    args = parser.parse_args()

    engine = create_engine(settings.APP_DATABASE_URL)

    # Verify connection before doing anything.
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"Cannot connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    create_tables(engine)

    if args.seed:
        seed(engine)


if __name__ == "__main__":
    main()
