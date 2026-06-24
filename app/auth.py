from __future__ import annotations

"""Local user authentication and bearer-token authorization helpers."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.database import create_user, get_user_by_id, get_user_by_username
from app.schemas import CurrentUser

security = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    """Hash a password with PBKDF2 so plaintext passwords are never stored."""
    password_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        password_salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"pbkdf2_sha256${password_salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, expected = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, expected)


def ensure_default_admin(settings: Settings) -> None:
    """Create default demo accounts when configured usernames are missing."""
    if not get_user_by_username(settings.admin_username):
        create_user(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
    if settings.user_username != settings.admin_username and not get_user_by_username(settings.user_username):
        create_user(
            username=settings.user_username,
            password_hash=hash_password(settings.user_password),
            role="user",
        )


def authenticate_user(username: str, password: str) -> CurrentUser | None:
    row = get_user_by_username(username)
    if row is None or not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return CurrentUser(id=row["id"], username=row["username"], role=row["role"])


def create_access_token(user: CurrentUser, settings: Settings | None = None) -> str:
    """Create a compact signed token without adding a JWT dependency."""
    current_settings = settings or get_settings()
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": int(time.time()) + current_settings.auth_token_expire_minutes * 60,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_part = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = sign_payload(payload_part, current_settings.auth_token_secret)
    return f"{payload_part}.{signature}"


def decode_access_token(token: str, settings: Settings | None = None) -> CurrentUser:
    current_settings = settings or get_settings()
    try:
        payload_part, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    expected = sign_payload(payload_part, current_settings.auth_token_secret)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid token")

    padded_payload = payload_part + "=" * (-len(payload_part) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded_payload.encode("ascii")))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")

    row = get_user_by_id(int(payload["sub"]))
    if row is None or not row["is_active"]:
        raise HTTPException(status_code=401, detail="User disabled")
    return CurrentUser(id=row["id"], username=row["username"], role=row["role"])


def sign_payload(payload_part: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_access_token(credentials.credentials)


def require_admin(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user
