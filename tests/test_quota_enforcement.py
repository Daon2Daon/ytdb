"""쿼터 강제 지점 라우터 테스트 — quota_service를 monkeypatch로 치환."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist
from app.services.quota_service import QuotaExceeded

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep


def test_create_group_quota_exceeded_400(monkeypatch):
    _as_user()

    async def _deny(session, user_id):
        raise QuotaExceeded("그룹 한도 초과: 현재 1개 / 한도 1개", limit=1, current=1)

    monkeypatch.setattr("app.routers.groups.check_group_quota", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups", json={"name": "새 그룹"})
    assert resp.status_code == 400
    assert "그룹 한도 초과" in resp.json()["detail"]
