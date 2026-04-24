"""
Admin user management CLI.

Usage:
    python scripts/create_admin.py <username>          # create admin
    python scripts/create_admin.py --list              # list all admins
    python scripts/create_admin.py --deactivate <username>  # revoke access
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import sqlalchemy
from sqlalchemy.orm import Session

from core.auth import hash_password
from core.config import settings
from core.rbac.models import AdminUser, Base


def _engine():
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


def _ensure_tables():
    Base.metadata.create_all(_engine())


def cmd_create(username: str) -> None:
    password = getpass.getpass(f"Password for '{username}': ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        sys.exit("Error: passwords do not match.")
    if len(password) < 8:
        sys.exit("Error: password must be at least 8 characters.")

    _ensure_tables()
    with Session(_engine()) as session:
        if session.query(AdminUser).filter_by(username=username).first():
            sys.exit(f"Error: admin '{username}' already exists.")
        session.add(AdminUser(username=username, hashed_password=hash_password(password)))
        session.commit()
    print(f"Admin '{username}' created.")


def cmd_list() -> None:
    _ensure_tables()
    with Session(_engine()) as session:
        admins = session.query(AdminUser).order_by(AdminUser.created_at).all()
    if not admins:
        print("No admin users found.")
        return
    for a in admins:
        status = "active" if a.is_active else "inactive"
        print(f"  {a.username:<20} {status:<10} created {a.created_at:%Y-%m-%d}")


def cmd_deactivate(username: str) -> None:
    _ensure_tables()
    with Session(_engine()) as session:
        admin = session.query(AdminUser).filter_by(username=username).first()
        if not admin:
            sys.exit(f"Error: admin '{username}' not found.")
        if not admin.is_active:
            sys.exit(f"Admin '{username}' is already inactive.")
        admin.is_active = False
        session.commit()
    print(f"Admin '{username}' deactivated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage HR Assistant admin users.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("username", nargs="?", help="Username to create")
    group.add_argument("--list", action="store_true", help="List all admin users")
    group.add_argument("--deactivate", metavar="USERNAME", help="Deactivate an admin user")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.deactivate:
        cmd_deactivate(args.deactivate)
    elif args.username:
        cmd_create(args.username)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
