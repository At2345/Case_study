"""CLI to manage users: python -m app.api.users add <name> <planner|admin|auditor> [tenant]"""
from __future__ import annotations

import getpass
import sys

from app.api.auth import UserStore
from app.bi.tenancy import DEFAULT_TENANT
from app.config import settings


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) not in (3, 4) or argv[0] != "add":
        print("usage: python -m app.api.users add <username> <planner|admin|auditor> [tenant]")
        return 2
    _, username, role, *rest = argv
    tenant = rest[0] if rest else DEFAULT_TENANT
    password = getpass.getpass("Password (min 10 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("passwords differ")
        return 1
    UserStore(settings.store_dir / "users.json").add_user(username, role, password, tenant)
    print(f"user '{username}' ({role}) in tenant '{tenant}' saved to "
          f"{settings.store_dir / 'users.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
