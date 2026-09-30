"""Manage workbench users from the command line.

    uv run python -m underwriting_agent.auth create --username ada --name "Ada L" --role admin
    uv run python -m underwriting_agent.auth list
    uv run python -m underwriting_agent.auth set-password --username ada
    uv run python -m underwriting_agent.auth deactivate --username ada

Passwords are typed at a hidden prompt (or piped in with --password-stdin for scripts), so
they never appear in shell history. Uses DATABASE_URL from .env, like the API.
"""

import argparse
import asyncio
import getpass
import sys

from underwriting_agent.auth import store
from underwriting_agent.auth.passwords import MIN_PASSWORD_LENGTH, check_password_strength
from underwriting_agent.auth.schemas import UserCreate, UserUpdate
from underwriting_agent.config import Settings
from underwriting_agent.persistence.db import create_engine, create_session_factory, init_db


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        # Windows PowerShell prefixes piped text with a byte-order mark and ends lines with \r\n.
        password = sys.stdin.readline().rstrip("\r\n").removeprefix("﻿")
    else:
        password = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
        if getpass.getpass("Repeat password: ") != password:
            raise SystemExit("Passwords do not match.")
    try:
        check_password_strength(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    return password


async def run(args: argparse.Namespace) -> None:
    engine = create_engine(Settings().database_url)
    await init_db(engine)
    try:
        async with create_session_factory(engine)() as session:
            if args.command == "list":
                users = await store.list_users(session)
                if not users:
                    print("No users yet. Create an admin with: create --role admin")
                for u in users:
                    state = "" if u.active else "  (inactive)"
                    print(f"{u.username:<20} {u.role:<12} {u.display_name}{state}")
            elif args.command == "create":
                user = await store.create_user(
                    session,
                    UserCreate(
                        username=args.username,
                        display_name=args.name,
                        role=args.role,
                        password=read_password(args.password_stdin),
                    ),
                )
                print(f"Created {user.role} {user.label}.")
            elif args.command == "set-password":
                user = await store.update_user(
                    session, args.username, UserUpdate(password=read_password(args.password_stdin))
                )
                if user is None:
                    raise SystemExit(f"No user {args.username!r}.")
                print(f"Password changed for {user.label}; their sessions were signed out.")
            elif args.command == "deactivate":
                user = await store.update_user(session, args.username, UserUpdate(active=False))
                if user is None:
                    raise SystemExit(f"No user {args.username!r}.")
                print(f"Deactivated {user.label}.")
    except store.UserExists as exc:
        raise SystemExit(f"User {exc} already exists.") from None
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m underwriting_agent.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list users")
    create = sub.add_parser("create", help="create a user")
    create.add_argument("--username", required=True, type=str.lower)
    create.add_argument("--name", required=True, help="display name, e.g. 'Ada Lovelace'")
    create.add_argument("--role", required=True, choices=["viewer", "underwriter", "admin"])
    for name in ("set-password", "deactivate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--username", required=True, type=str.lower)
    for cmd in (create, sub.choices["set-password"]):
        cmd.add_argument(
            "--password-stdin", action="store_true", help="read the password from stdin"
        )
    try:
        asyncio.run(run(parser.parse_args()))
    except ValueError as exc:  # e.g. an invalid username
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
