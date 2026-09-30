"""Users and sessions in the application database."""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.auth.passwords import (
    DUMMY_HASH,
    check_password_strength,
    hash_password,
    verify_password,
)
from underwriting_agent.auth.schemas import User, UserCreate, UserUpdate
from underwriting_agent.persistence.db import SessionRow, UserRow
from underwriting_agent.utils import utcnow


class UserExists(Exception):
    pass


def _to_user(row: UserRow) -> User:
    return User(
        username=row.username, display_name=row.display_name, role=row.role, active=row.active
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    check_password_strength(data.password)
    if await session.get(UserRow, data.username) is not None:
        raise UserExists(data.username)
    row = UserRow(
        username=data.username,
        display_name=data.display_name,
        role=data.role,
        password_hash=hash_password(data.password),
        active=True,
        created_at=utcnow(),
    )
    session.add(row)
    await session.commit()
    return _to_user(row)


async def list_users(session: AsyncSession) -> list[User]:
    rows = await session.execute(select(UserRow).order_by(UserRow.username))
    return [_to_user(r) for r in rows.scalars()]


async def get_user(session: AsyncSession, username: str) -> User | None:
    row = await session.get(UserRow, username)
    return _to_user(row) if row else None


async def update_user(session: AsyncSession, username: str, data: UserUpdate) -> User | None:
    row = await session.get(UserRow, username)
    if row is None:
        return None
    if data.password is not None:
        check_password_strength(data.password)
        row.password_hash = hash_password(data.password)
    if data.display_name is not None:
        row.display_name = data.display_name.strip()
    if data.role is not None:
        row.role = data.role
    if data.active is not None:
        row.active = data.active
    # A password reset, a deactivation or a role change signs the user out everywhere.
    if data.password is not None or data.active is False or data.role is not None:
        await session.execute(delete(SessionRow).where(SessionRow.username == username))
    await session.commit()
    return _to_user(row)


async def count_active_admins(session: AsyncSession) -> int:
    rows = await session.execute(
        select(UserRow).where(UserRow.role == "admin", UserRow.active.is_(True))
    )
    return len(rows.scalars().all())


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    row = await session.get(UserRow, username)
    # Always run one hash check, so unknown usernames take as long as wrong passwords.
    ok = verify_password(password, row.password_hash if row else DUMMY_HASH)
    if row is None or not ok or not row.active:
        return None
    return _to_user(row)


async def create_session(
    session: AsyncSession, username: str, lifetime: timedelta
) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + lifetime
    # Clear this user's expired sessions while we are here.
    await session.execute(
        delete(SessionRow).where(SessionRow.username == username, SessionRow.expires_at < now)
    )
    session.add(
        SessionRow(
            token_hash=_token_hash(token), username=username, created_at=now, expires_at=expires
        )
    )
    await session.commit()
    return token, expires


async def user_for_token(session: AsyncSession, token: str) -> User | None:
    row = await session.get(SessionRow, _token_hash(token))
    if row is None or row.expires_at <= utcnow():
        return None
    user = await session.get(UserRow, row.username)
    return _to_user(user) if user and user.active else None


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(SessionRow).where(SessionRow.token_hash == _token_hash(token)))
    await session.commit()
