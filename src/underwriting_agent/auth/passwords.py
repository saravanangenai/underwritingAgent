"""Password hashing with scrypt from the standard library (no extra dependency).

Stored format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``, so the cost parameters can be
raised later and old hashes still verify.
"""

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2**14, 8, 1, 64  # ~16 MiB of memory per hash
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        raise ValueError("Password must not start or end with whitespace")


# Verifying against this when the username does not exist keeps login timing the same,
# so response time does not reveal which usernames exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
