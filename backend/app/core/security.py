"""Password hashing, session tokens, CSRF tokens, and encryption at rest."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_hasher = PasswordHasher()
JWT_ALGORITHM = "HS256"
SESSION_COOKIE = "ap_session"
CSRF_COOKIE = "ap_csrf"
CSRF_HEADER = "X-CSRF-Token"

# Verifying against this equalises response time for unknown e-mail addresses.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def create_session_token(user_id: uuid.UUID, session_id: uuid.UUID, expires_at: datetime) -> str:
    claims = {"sub": str(user_id), "sid": str(session_id), "exp": expires_at, "iat": datetime.now(UTC)}
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub", "sid"]})
    except jwt.PyJWTError:
        return None


def session_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=settings.session_hours)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    return bool(cookie_value and header_value and hmac.compare_digest(cookie_value, header_value))


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential could not be decrypted; reconnect the integration.") from exc


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge
