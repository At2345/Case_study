"""CLI to manage users: python -m app.api.users add <name> <planner|admin|auditor>"""
from __future__ import annotations

import getpass
import sys

from app.api.auth import UserStore
from app.config import settings


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 3 or argv[0] != "add":
        print("usage: python -m app.api.users add <username> <planner|admin|auditor>")
        return 2
    _, username, role = argv
    password = getpass.getpass("Password (min 10 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("passwords differ")
        return 1
    UserStore(settings.store_dir / "users.json").add_user(username, role, password)
    print(f"user '{username}' ({role}) saved to {settings.store_dir / 'users.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
