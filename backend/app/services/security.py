from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.security import AdminUserRecord, RuntimeConfigRecord, SecretRecord

SESSION_COOKIE = "talos_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise RuntimeError("Stored secret cannot be decrypted with the current SECRET_KEY") from exc
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def save_secret(provider: str, payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
    clean = {str(k): v for k, v in payload.items() if v not in (None, "")}
    with SessionLocal() as db:
        row = db.query(SecretRecord).filter(SecretRecord.provider == provider).first()
        if not row:
            row = SecretRecord(provider=provider, encrypted_payload=encrypt_json(clean), metadata_json=metadata or {})
            db.add(row)
        else:
            row.encrypted_payload = encrypt_json(clean)
            row.metadata_json = metadata or row.metadata_json or {}
            row.updated_at = utcnow()
        db.commit()


def get_secret(provider: str) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.query(SecretRecord).filter(SecretRecord.provider == provider).first()
        if not row:
            return {}
        return decrypt_json(row.encrypted_payload)


def delete_secret(provider: str) -> bool:
    with SessionLocal() as db:
        row = db.query(SecretRecord).filter(SecretRecord.provider == provider).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True


def list_secret_status() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(SecretRecord).order_by(SecretRecord.provider.asc()).all()
        out = []
        for row in rows:
            try:
                keys = sorted(get_secret(row.provider).keys())
            except Exception:
                keys = []
            out.append({"provider": row.provider, "configured": True, "fields": keys, "updated_at": row.updated_at})
        return out


def set_runtime(key: str, value: dict[str, Any]) -> None:
    with SessionLocal() as db:
        row = db.get(RuntimeConfigRecord, key)
        if not row:
            row = RuntimeConfigRecord(key=key, value_json=value)
            db.add(row)
        else:
            row.value_json = value
            row.updated_at = utcnow()
        db.commit()


def get_runtime(key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(RuntimeConfigRecord, key)
        return dict(row.value_json or {}) if row else dict(default or {})


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_bytes = base64.urlsafe_b64decode(salt.encode("ascii")) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 310_000)
    return base64.urlsafe_b64encode(salt_bytes).decode("ascii"), base64.urlsafe_b64encode(digest).decode("ascii")


def verify_password(password: str, salt: str, expected: str) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def admin_exists(db: Session | None = None) -> bool:
    if db is not None:
        return db.query(AdminUserRecord).count() > 0
    with SessionLocal() as own:
        return own.query(AdminUserRecord).count() > 0


def create_admin(setup_key: str, password: str) -> None:
    if not hmac.compare_digest(setup_key, settings.secret_key):
        raise ValueError("Setup key is incorrect")
    if len(password) < 10:
        raise ValueError("Admin password must be at least 10 characters")
    with SessionLocal() as db:
        if admin_exists(db):
            raise ValueError("Admin is already configured")
        salt, digest = hash_password(password)
        db.add(AdminUserRecord(username="admin", password_salt=salt, password_hash=digest))
        db.commit()


def authenticate(password: str) -> bool:
    with SessionLocal() as db:
        row = db.query(AdminUserRecord).filter(AdminUserRecord.username == "admin").first()
        return bool(row and verify_password(password, row.password_salt, row.password_hash))


def issue_session() -> str:
    payload = {"sub": "admin", "exp": int(time.time()) + SESSION_TTL_SECONDS}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def validate_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return payload.get("sub") == "admin" and int(payload.get("exp", 0)) > int(time.time())
    except Exception:
        return False
