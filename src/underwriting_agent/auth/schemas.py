from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["viewer", "underwriter", "admin"]

# Each role can do everything the roles before it can.
ROLE_RANK: dict[str, int] = {"viewer": 0, "underwriter": 1, "admin": 2}

USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"


class User(BaseModel):
    username: str
    display_name: str
    role: Role
    active: bool = True

    @property
    def label(self) -> str:
        """How the user is named in audit records: 'Display Name (username)'."""
        return f"{self.display_name} ({self.username})"


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class LoginResponse(BaseModel):
    user: User
    token: str  # for API clients; browsers use the httpOnly cookie set alongside it
    expires_at: datetime


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=2, max_length=100)
    role: Role
    password: str = Field(max_length=256)

    @field_validator("display_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("must be at least 2 characters")
        return value


class UserUpdate(BaseModel):
    """Change a user's role, activate or deactivate them, or reset their password."""

    display_name: str | None = Field(default=None, min_length=2, max_length=100)
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)
