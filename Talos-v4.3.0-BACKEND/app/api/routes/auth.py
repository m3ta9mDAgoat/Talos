from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.security import SESSION_COOKIE, admin_exists, authenticate, create_admin, issue_session, validate_session

router = APIRouter(prefix="/auth", tags=["auth"])


class SetupRequest(BaseModel):
    setup_key: str
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    password: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.app_env.lower() == "production",
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


@router.get("/status")
def status(request: Request):
    configured = admin_exists()
    return {
        "setup_required": not configured,
        "authenticated": validate_session(request.cookies.get(SESSION_COOKIE)) if configured else False,
    }


@router.post("/setup")
def setup(req: SetupRequest, response: Response):
    try:
        create_admin(req.setup_key, req.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_cookie(response, issue_session())
    return {"ok": True}


@router.post("/login")
def login(req: LoginRequest, response: Response):
    if not authenticate(req.password):
        raise HTTPException(401, "Invalid password")
    _set_cookie(response, issue_session())
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
