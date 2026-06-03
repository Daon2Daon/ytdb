"""단일 계정 로그인 인증 (httpOnly 세션 쿠키).

AUTH_PASSWORD가 비어 있으면 인증 비활성(개발). 값이 설정되면 require_auth가
모든 보호 라우터에서 세션을 강제한다. 자격증명 비교는 상수시간(secrets).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.config import settings as app_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def auth_enabled() -> bool:
    return bool((app_settings.AUTH_PASSWORD or "").strip())


def require_auth(request: Request) -> None:
    """보호 라우터 의존성. 인증 비활성이면 통과, 활성이면 세션 필요."""
    if not auth_enabled():
        return
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/me")
async def me(request: Request) -> dict:
    enabled = auth_enabled()
    user = request.session.get("user") if enabled else None
    return {
        "auth_enabled": enabled,
        # 비활성이면 항상 인증된 것으로 취급(프론트가 앱을 바로 띄움).
        "authenticated": True if not enabled else bool(user),
        "username": user,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request) -> dict:
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="인증이 설정되지 않았습니다.")
    ok_user = secrets.compare_digest(payload.username, app_settings.AUTH_USERNAME)
    ok_pw = secrets.compare_digest(payload.password, app_settings.AUTH_PASSWORD)
    if not (ok_user and ok_pw):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    request.session["user"] = payload.username
    return {"username": payload.username}


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)
