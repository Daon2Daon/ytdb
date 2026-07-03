"""DB 기반 다중 사용자 인증 검증 (DB 없이 FakeSession/monkeypatch로 대체).

- 개발 모드(users 없음 + AUTH_PASSWORD 미설정): 인증 비활성, require_user는 가상 admin.
- 활성 모드: 미로그인 401, 로그인은 users 테이블 조회(argon2 검증).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.control_db import get_session
from app.main import app
from app.models.control.user import User
from app.routers import auth as auth_router
from app.services.auth_service import hash_password, set_users_exist


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """session.execute() 호출 순서대로 미리 준비한 값을 돌려준다."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        return FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "user_id", None) is None:
                obj.user_id = 999

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


def make_user(**kw) -> User:
    defaults = dict(
        user_id=1, email="alice@example.com", password_hash=hash_password("pw1234"),
        display_name="Alice", role="user", status="active", plan_id=1,
        last_login_at=None, created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    u = User()
    for k, v in defaults.items():
        setattr(u, k, v)
    return u


def override_session(fake: FakeSession):
    async def _dep():
        yield fake
    app.dependency_overrides[get_session] = _dep


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    set_users_exist(False)
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---- 개발 모드 ----

def test_me_disabled_dev_mode():
    body = _client().get("/api/auth/me").json()
    assert body["auth_enabled"] is False and body["authenticated"] is True
    assert body["user"]["role"] == "admin"  # 가상 admin


def test_protected_open_when_disabled():
    assert _client().get("/api/groups").status_code != 401


# ---- 활성 모드 ----

def test_protected_requires_login_when_enabled():
    set_users_exist(True)
    c = _client()
    assert c.get("/api/groups").status_code == 401
    assert c.get("/api/auth/me").json()["authenticated"] is False


def test_login_success_and_me(monkeypatch):
    set_users_exist(True)
    user = make_user()
    fake = FakeSession([user])          # login의 email 조회 1회
    override_session(fake)

    async def fake_load(user_id):
        return user if user_id == 1 else None

    monkeypatch.setattr(auth_router, "_load_user", fake_load)

    c = _client()
    r = c.post("/api/auth/login", json={"email": "Alice@Example.com", "password": "pw1234"})
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    assert fake.committed is True       # last_login_at 갱신 커밋
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["user"]["role"] == "user"
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/groups").status_code == 401


def test_login_wrong_password():
    set_users_exist(True)
    override_session(FakeSession([make_user()]))
    r = _client().post("/api/auth/login", json={"email": "alice@example.com", "password": "no"})
    assert r.status_code == 401


def test_login_unknown_email():
    set_users_exist(True)
    override_session(FakeSession([None]))
    r = _client().post("/api/auth/login", json={"email": "who@example.com", "password": "x"})
    assert r.status_code == 401


def test_suspended_user_rejected_at_login():
    set_users_exist(True)
    override_session(FakeSession([make_user(status="suspended")]))
    r = _client().post("/api/auth/login", json={"email": "alice@example.com", "password": "pw1234"})
    assert r.status_code == 403


def test_suspended_user_rejected_at_request(monkeypatch):
    set_users_exist(True)
    active = make_user()
    override_session(FakeSession([active]))
    state = {"user": active}

    async def fake_load(user_id):
        return state["user"]

    monkeypatch.setattr(auth_router, "_load_user", fake_load)
    c = _client()
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": "pw1234"})
    state["user"] = make_user(status="suspended")   # 로그인 후 정지됨
    assert c.get("/api/groups").status_code == 403


def test_health_and_root_open_without_auth():
    set_users_exist(True)
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code in (200, 503)
