"""Phase 9: sign-in, sessions, roles and user management."""

import asyncio
import io
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, sign_in
from underwriting_agent.api.main import create_app
from underwriting_agent.auth import store
from underwriting_agent.auth.deps import SESSION_COOKIE, LoginThrottle
from underwriting_agent.auth.passwords import hash_password, verify_password
from underwriting_agent.persistence.db import create_engine, create_session_factory
from underwriting_agent.samples import load_sample

# --- passwords ---


def test_password_hash_round_trip():
    stored = hash_password("a long enough secret")
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password("a long enough secret", stored)
    assert not verify_password("a long enough secreT", stored)
    assert hash_password("a long enough secret") != stored  # salted
    assert not verify_password("x", "not-a-hash")


# --- who can see what ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/submissions"),
        ("get", "/submissions/SUB-1"),
        ("get", "/submissions/SUB-1/decision"),
        ("get", "/submissions/SUB-1/history"),
        ("get", "/audit/verify"),
        ("get", "/auth/me"),
        ("get", "/users"),
        ("post", "/underwrite"),
        ("post", "/submissions/SUB-1/review"),
    ],
)
def test_everything_but_health_needs_sign_in(anon_client, method, path):
    response = getattr(anon_client, method)(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert anon_client.get("/health").status_code == 200


def test_viewer_can_read_but_not_submit(anon_client):
    sign_in(anon_client, "vic")
    assert anon_client.get("/submissions").status_code == 200
    assert anon_client.get("/audit/verify").status_code == 200
    response = anon_client.post("/underwrite", json=load_sample("clean"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Requires the underwriter role"


def test_submission_records_who_submitted_it(client):
    body = client.post("/underwrite", json=load_sample("clean")).json()
    sid = body["submission_id"]
    assert client.get(f"/submissions/{sid}").json()["submitted_by"] == "uma"
    assert client.get(f"/submissions/{sid}/decision").json()["inputs"]["submitted_by"] == "uma"


# --- sign-in and sessions ---


def test_login_sets_an_httponly_cookie_that_works_on_its_own(anon_client):
    response = anon_client.post("/auth/login", json={"username": "uma", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "username": "uma",
        "display_name": "Uma Underwriter",
        "role": "underwriter",
        "active": True,
    }
    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert anon_client.get("/auth/me").json()["username"] == "uma"  # via the cookie


def test_usernames_are_case_insensitive(anon_client):
    response = anon_client.post("/auth/login", json={"username": " UMA ", "password": PASSWORD})
    assert response.status_code == 200


@pytest.mark.parametrize("username", ["uma", "nobody"])
def test_wrong_credentials_get_one_generic_answer(anon_client, username):
    response = anon_client.post(
        "/auth/login", json={"username": username, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_repeated_failures_lock_the_username(anon_client):
    for _ in range(5):
        anon_client.post("/auth/login", json={"username": "uma", "password": "wrong-password"})
    locked = anon_client.post("/auth/login", json={"username": "uma", "password": PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    # Other users are unaffected.
    assert (
        anon_client.post("/auth/login", json={"username": "vic", "password": PASSWORD}).status_code
        == 200
    )


def test_throttle_unlocks_after_the_lockout():
    now = [0.0]
    throttle = LoginThrottle(max_failures=2, lockout_seconds=60, clock=lambda: now[0])
    throttle.failed("u")
    assert throttle.retry_after("u") == 0
    throttle.failed("u")
    assert throttle.retry_after("u") == 60
    now[0] = 61
    assert throttle.retry_after("u") == 0


def test_logout_ends_the_session(client):
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_expired_sessions_are_rejected(settings, anon_client):
    async def make_expired() -> str:
        engine = create_engine(settings.database_url)
        async with create_session_factory(engine)() as session:
            token, _ = await store.create_session(session, "uma", timedelta(seconds=-1))
        await engine.dispose()
        return token

    token = asyncio.run(make_expired())
    response = anon_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_bogus_token_is_rejected(anon_client):
    assert anon_client.get("/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- user management (admin) ---


def test_only_admins_manage_users(client):
    assert client.get("/users").status_code == 403  # uma is an underwriter


def test_admin_creates_and_lists_users(anon_client):
    sign_in(anon_client, "ada")
    new = {
        "username": "neil",
        "display_name": "Neil New",
        "role": "viewer",
        "password": "another-long-pass",
    }
    response = anon_client.post("/users", json=new)
    assert response.status_code == 201
    assert response.json()["role"] == "viewer"
    assert [u["username"] for u in anon_client.get("/users").json()] == [
        "ada",
        "neil",
        "uma",
        "vic",
    ]
    assert anon_client.post("/users", json=new).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        {"password": "short"},  # under 12 characters
        {"username": "Bad Name!"},  # not a valid username
        {"role": "superuser"},
    ],
)
def test_invalid_new_users_are_rejected(anon_client, change):
    sign_in(anon_client, "ada")
    new = {
        "username": "neil",
        "display_name": "Neil New",
        "role": "viewer",
        "password": "another-long-pass",
    }
    assert anon_client.post("/users", json={**new, **change}).status_code == 422


def test_deactivating_a_user_signs_them_out(settings, users):
    with TestClient(create_app(settings)) as admin, TestClient(create_app(settings)) as uma:
        sign_in(admin, "ada")
        sign_in(uma, "uma")
        assert admin.patch("/users/uma", json={"active": False}).json()["active"] is False
        assert uma.get("/auth/me").status_code == 401
        login = uma.post("/auth/login", json={"username": "uma", "password": PASSWORD})
        assert login.status_code == 401


def test_password_reset_signs_the_user_out(settings, users):
    with TestClient(create_app(settings)) as admin, TestClient(create_app(settings)) as vic:
        sign_in(admin, "ada")
        sign_in(vic, "vic")
        assert (
            admin.patch("/users/vic", json={"password": "a-brand-new-password"}).status_code == 200
        )
        assert vic.get("/auth/me").status_code == 401
        login = vic.post(
            "/auth/login", json={"username": "vic", "password": "a-brand-new-password"}
        )
        assert login.status_code == 200


def test_the_last_admin_cannot_be_removed(anon_client):
    sign_in(anon_client, "ada")
    assert anon_client.patch("/users/ada", json={"role": "viewer"}).status_code == 409
    assert anon_client.patch("/users/ada", json={"active": False}).status_code == 409
    anon_client.patch("/users/uma", json={"role": "admin"})
    assert anon_client.patch("/users/ada", json={"role": "viewer"}).status_code == 200


def test_updating_an_unknown_user_is_404(anon_client):
    sign_in(anon_client, "ada")
    assert anon_client.patch("/users/nobody", json={"active": False}).status_code == 404


# --- command line ---


def test_cli_creates_and_lists_users(settings, monkeypatch, capsys):
    from underwriting_agent.auth.__main__ import main

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setattr(sys, "stdin", io.StringIO("cli-password-123\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auth",
            "create",
            "--username",
            "Carl",
            "--name",
            "Carl CLI",
            "--role",
            "admin",
            "--password-stdin",
        ],
    )
    main()
    assert "Created admin Carl CLI (carl)." in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["auth", "list"])
    main()
    assert "carl" in capsys.readouterr().out

    # A password piped from Windows PowerShell arrives with a BOM and \r\n; both are dropped.
    monkeypatch.setattr(sys, "stdin", io.StringIO("﻿piped-password-1\r\n"))
    monkeypatch.setattr(
        sys, "argv", ["auth", "set-password", "--username", "carl", "--password-stdin"]
    )
    main()

    async def check() -> bool:
        engine = create_engine(settings.database_url)
        async with create_session_factory(engine)() as session:
            user = await store.authenticate(session, "carl", "piped-password-1")
        await engine.dispose()
        return user is not None

    assert asyncio.run(check())

    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    monkeypatch.setattr(
        sys, "argv", ["auth", "set-password", "--username", "carl", "--password-stdin"]
    )
    with pytest.raises(SystemExit, match="at least 12 characters"):
        main()
