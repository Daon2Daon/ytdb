"""초대 토큰 가입 플로우 (FakeSession, DB 불필요)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.control_db import get_session
from app.main import app
from app.models.control.invitation import Invitation
from app.services.auth_service import set_users_exist
from tests.test_auth import FakeSession, override_session


def make_invite(**kw) -> Invitation:
    inv = Invitation()
    defaults = dict(
        invite_id=10, token="tok-valid", plan_id=1, memo=None, invited_by=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        used_by=None, used_at=None, created_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(inv, k, v)
    return inv


@pytest.fixture(autouse=True)
def _reset():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _payload(**kw):
    base = {"token": "tok-valid", "email": "bob@example.com",
            "password": "pw123456", "display_name": "Bob"}
    base.update(kw)
    return base


def test_signup_success_and_autologin():
    invite = make_invite()
    fake = FakeSession([invite, None])   # 1) 토큰 조회 → invite, 2) 이메일 중복 조회 → 없음
    override_session(fake)
    c = _client()
    r = c.post("/api/auth/signup", json=_payload())
    assert r.status_code == 201
    assert r.json()["email"] == "bob@example.com" and r.json()["role"] == "user"
    assert invite.used_at is not None and invite.used_by == 999
    assert fake.committed is True
    # 자동 로그인: 세션 쿠키가 발급됨 (_load_user는 실 DB 경로라 여기서는 쿠키 존재만 확인).
    assert c.cookies.get("session")


def test_signup_unknown_token():
    override_session(FakeSession([None]))
    r = _client().post("/api/auth/signup", json=_payload(token="nope"))
    assert r.status_code == 400


def test_signup_expired_token():
    invite = make_invite(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    override_session(FakeSession([invite]))
    r = _client().post("/api/auth/signup", json=_payload())
    assert r.status_code == 400


def test_signup_used_token():
    invite = make_invite(used_at=datetime.now(timezone.utc), used_by=5)
    override_session(FakeSession([invite]))
    r = _client().post("/api/auth/signup", json=_payload())
    assert r.status_code == 400


def test_signup_duplicate_email():
    from tests.test_auth import make_user
    override_session(FakeSession([make_invite(), make_user()]))
    r = _client().post("/api/auth/signup", json=_payload(email="alice@example.com"))
    assert r.status_code == 409


def test_signup_short_password_rejected():
    # 검증 실패(422) 경로도 get_session 의존성은 해석되므로, DB 없는 환경에서
    # 엔진 생성 500을 피하기 위해 세션을 오버라이드한다(핸들러는 실행되지 않음).
    override_session(FakeSession([]))
    r = _client().post("/api/auth/signup", json=_payload(password="short"))
    assert r.status_code == 422
