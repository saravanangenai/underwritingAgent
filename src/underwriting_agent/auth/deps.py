"""FastAPI dependencies: who is calling, and may they do this?"""

import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.auth import store
from underwriting_agent.auth.schemas import ROLE_RANK, User

SESSION_COOKIE = "uw_session"


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


def request_token(request: Request) -> str | None:
    """The session token from the cookie (browsers) or an Authorization: Bearer header (API
    clients)."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = request_token(request)
    user = await store.user_for_token(session, token) if token else None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(minimum: str):
    """Allow users whose role is `minimum` or higher (viewer < underwriter < admin)."""

    async def check(user: User = Depends(current_user)) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail=f"Requires the {minimum} role")
        return user

    return check


# Shared instances, so routes can write Depends(require_underwriter).
require_viewer = require_role("viewer")
require_underwriter = require_role("underwriter")
require_admin = require_role("admin")


class LoginThrottle:
    """Locks a username for a while after repeated failed sign-ins (per API process)."""

    def __init__(self, max_failures: int, lockout_seconds: float, clock=time.monotonic):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, username: str) -> float:
        remaining = self._locked_until.get(username, 0) - self.clock()
        return max(0.0, remaining)

    def failed(self, username: str) -> None:
        count = self._failures.get(username, 0) + 1
        self._failures[username] = count
        if count >= self.max_failures:
            self._locked_until[username] = self.clock() + self.lockout_seconds
            self._failures[username] = 0

    def succeeded(self, username: str) -> None:
        self._failures.pop(username, None)
        self._locked_until.pop(username, None)
