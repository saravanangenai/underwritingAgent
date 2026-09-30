from datetime import timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from underwriting_agent.auth import store
from underwriting_agent.auth.deps import (
    SESSION_COOKIE,
    current_user,
    get_session,
    request_token,
    require_admin,
)
from underwriting_agent.auth.schemas import (
    LoginRequest,
    LoginResponse,
    User,
    UserCreate,
    UserUpdate,
)

log = structlog.get_logger()
router = APIRouter()

INVALID_LOGIN = "Invalid username or password"


@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Sign in. Sets an httpOnly session cookie and also returns the token for API clients."""
    settings = request.app.state.settings
    throttle = request.app.state.login_throttle
    username = body.username.strip().lower()

    wait = throttle.retry_after(username)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-ins. Try again later.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    user = await store.authenticate(session, username, body.password)
    if user is None:
        throttle.failed(username)
        log.warning("login_failed", username=username)
        raise HTTPException(status_code=401, detail=INVALID_LOGIN)

    throttle.succeeded(username)
    token, expires = await store.create_session(
        session, user.username, timedelta(hours=settings.session_hours)
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(settings.session_hours * 3600),
        httponly=True,  # page scripts cannot read it
        secure=settings.cookie_secure,  # true in production (HTTPS)
        samesite="lax",  # not sent on cross-site POSTs
        path="/",
    )
    log.info("login", username=user.username, role=user.role)
    return LoginResponse(user=user, token=token, expires_at=expires)


@router.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    token = request_token(request)
    if token:
        await store.delete_session(session, token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me", response_model=User, tags=["auth"])
async def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[User], tags=["users"])
async def list_users(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    return await store.list_users(session)


@router.post("/users", response_model=User, status_code=201, tags=["users"])
async def create_user(
    body: UserCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        user = await store.create_user(session, body)
    except store.UserExists:
        raise HTTPException(
            status_code=409, detail=f"User {body.username} already exists"
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    log.info("user_created", username=user.username, role=user.role, by=admin.username)
    return user


@router.patch("/users/{username}", response_model=User, tags=["users"])
async def update_user(
    username: str,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    existing = await store.get_user(session, username)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    losing_admin = (
        existing.role == "admin"
        and existing.active
        and (body.active is False or (body.role is not None and body.role != "admin"))
    )
    if losing_admin and await store.count_active_admins(session) <= 1:
        raise HTTPException(status_code=409, detail="Cannot remove the last active admin")
    try:
        user = await store.update_user(session, username, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    log.info(
        "user_updated",
        username=username,
        by=admin.username,
        fields=sorted(body.model_dump(exclude_none=True, exclude={"password"}))
        + (["password"] if body.password else []),
    )
    return user
