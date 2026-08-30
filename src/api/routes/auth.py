"""Auth endpoints — validate, login, change-password, logout-all."""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from src.database.connector import DatabaseConnector

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _get_credentials(db) -> tuple[str, int] | None:
    """Returns (password_hash, token_version) or None if table is empty."""
    row = db.execute(
        "SELECT password_hash, token_version FROM auth_credentials WHERE id = 1"
    ).fetchone()
    return (row[0], row[1]) if row else None


@router.get("/auth/validate")
async def validate_auth():
    """Returns 200 if the request is authenticated. Used by health checks."""
    return {"ok": True}


@router.post("/auth/login")
async def login(body: LoginRequest):
    """Verify password, return versioned bearer token. Exempt from auth middleware.

    Reads credentials from the in-memory cache (auth_cache) — never opens a DB
    connection. This prevents DuckDB read-only/read-write conflict errors that
    lock out users while a sync is running.
    """
    from src.api import auth_cache
    creds = auth_cache.get()
    if creds is None or not creds.configured:
        raise HTTPException(status_code=503, detail="Auth not configured")
    try:
        valid = bcrypt.checkpw(body.password.encode(), creds.password_hash.encode())
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Auth credentials corrupted")
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": f"{body.password}.{creds.token_version}"}


@router.post("/auth/change-password")
async def change_password(body: ChangePasswordRequest):
    """Verify current password, hash new one, bump version. Returns new token (caller stays logged in)."""
    if "." in body.new_password:
        raise HTTPException(status_code=400, detail="Password may not contain a period (.)")
    db = DatabaseConnector()
    try:
        creds = _get_credentials(db)
        if creds is None:
            raise HTTPException(status_code=503, detail="Auth not configured")
        pw_hash, _ = creds
        try:
            valid = bcrypt.checkpw(body.current_password.encode(), pw_hash.encode())
        except (ValueError, TypeError):
            raise HTTPException(status_code=503, detail="Auth credentials corrupted")
        if not valid:
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        new_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt()).decode()
        result = db.execute(
            "UPDATE auth_credentials SET password_hash = ?, token_version = token_version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1 RETURNING token_version",
            [new_hash]
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=503, detail="Auth credentials row missing")
        new_version = result[0]
        # Refresh cache immediately so the new hash/version is active and the
        # old password/token is invalidated from this moment forward.
        from src.api import auth_cache
        auth_cache.refresh_from_db(db)
    finally:
        db.close()
    return {"token": f"{body.new_password}.{new_version}"}


@router.post("/auth/logout-all")
async def logout_all():
    """Bump token version — invalidates all existing tokens including the caller's."""
    db = DatabaseConnector()
    try:
        db.execute(
            "UPDATE auth_credentials SET token_version = token_version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
        )
        # Refresh cache immediately so all in-flight tokens (including the
        # caller's) are invalidated from this moment forward.
        from src.api import auth_cache
        auth_cache.refresh_from_db(db)
    finally:
        db.close()
    return {"ok": True}
