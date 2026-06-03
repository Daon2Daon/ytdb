"""로그인 인증(단일 계정·세션 쿠키) 검증.

AUTH_PASSWORD 미설정 시 인증 비활성(개방), 설정 시 강제. 보호 라우트는 미로그인 401.
DB 의존 엔드포인트는 미로그인 시 require_auth가 먼저 401을 반환하므로 DB에 닿지 않는다.
인증 후엔 DB 미설정으로 다른 상태가 될 수 있어 '401이 아님'으로만 확인한다.
"""

from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.main import app


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_me_disabled_when_no_password(monkeypatch):
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    r = _client().get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_enabled"] is False
    assert body["authenticated"] is True


def test_protected_open_when_disabled(monkeypatch):
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    assert _client().get("/api/groups").status_code != 401


def test_protected_requires_login_when_enabled(monkeypatch):
    monkeypatch.setattr(app_settings, "AUTH_USERNAME", "admin")
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "secret123")
    c = _client()
    assert c.get("/api/groups").status_code == 401
    assert c.get("/api/auth/me").json()["authenticated"] is False


def test_login_logout_flow(monkeypatch):
    monkeypatch.setattr(app_settings, "AUTH_USERNAME", "admin")
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "secret123")
    c = _client()
    assert c.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "secret123"}).status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["username"] == "admin"
    assert c.get("/api/groups").status_code != 401  # 인증 통과(이후 DB 사유로 다른 상태 가능)
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/groups").status_code == 401


def test_health_and_root_open_without_auth(monkeypatch):
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "secret123")
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code in (200, 503)
