"""관리자 사용자 관리 API: 라우트 등록·자기 정지 가드·권한."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_admin_user_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users/{user_id}" in paths
    assert "/api/admin/users/{user_id}/limits" in paths
    assert "/api/admin/users/{user_id}/temp-password" in paths
    assert "/api/admin/plans/{plan_id}" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.patch("/api/admin/users/2", json={}).status_code == 403
    assert c.put("/api/admin/users/2/limits", json={}).status_code == 403
    assert c.post("/api/admin/users/2/temp-password").status_code == 403
    assert c.patch("/api/admin/plans/1", json={}).status_code == 403


def test_admin_cannot_suspend_self():
    async def _dep():
        return ADMIN
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.patch("/api/admin/users/1", json={"status": "suspended"})
    assert resp.status_code == 400
    assert "자기 자신" in resp.json()["detail"]


async def test_patch_user_sets_and_clears_expiry(monkeypatch):
    """만료일 설정·해제 + notified 리셋(tri-state). 세션은 가짜 — 실 SQL은 E2E."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    fake_user = SimpleNamespace(
        user_id=2, email="b@x.com", display_name="B", role="user", status="active",
        plan_id=1, plan_expires_at=None,
        plan_expiry_notified_at=datetime.now(timezone.utc),
        last_login_at=None, created_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        async def get(self, model, pk):
            return fake_user

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    async def _dep():
        return ADMIN

    # get_session 의존성의 실제 함수 객체로 override — admin.py 상단 import에서 확인
    from app.routers.admin import get_session as admin_get_session

    async def _sess():
        return FakeSession()

    app.dependency_overrides[require_user] = _dep
    app.dependency_overrides[admin_get_session] = _sess
    c = TestClient(app, raise_server_exceptions=False)

    # 설정: 값 → 반영 + notified 리셋
    r = c.patch("/api/admin/users/2", json={"plan_expires_at": "2026-08-15T00:00:00Z"})
    assert r.status_code == 200
    assert fake_user.plan_expires_at is not None
    assert fake_user.plan_expiry_notified_at is None
    assert r.json()["plan_expires_at"] is not None

    # 해제: 명시적 null → NULL + notified 리셋
    fake_user.plan_expiry_notified_at = datetime.now(timezone.utc)
    r = c.patch("/api/admin/users/2", json={"plan_expires_at": None})
    assert r.status_code == 200
    assert fake_user.plan_expires_at is None
    assert fake_user.plan_expiry_notified_at is None

    # 필드 생략 → 만료일 변경 없음 (tri-state)
    sentinel = datetime(2030, 1, 1, tzinfo=timezone.utc)
    fake_user.plan_expires_at = sentinel
    r = c.patch("/api/admin/users/2", json={"status": "active"})
    assert r.status_code == 200
    assert fake_user.plan_expires_at is sentinel


async def test_patch_user_plan_change_resets_notified():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    fake_user = SimpleNamespace(
        user_id=2, email="b@x.com", display_name="B", role="user", status="active",
        plan_id=1, plan_expires_at=None,
        plan_expiry_notified_at=datetime.now(timezone.utc),
        last_login_at=None, created_at=datetime.now(timezone.utc),
    )
    fake_plan = SimpleNamespace(plan_id=3)

    class FakeSession:
        async def get(self, model, pk):
            from app.models.control.user import User as UserModel

            return fake_user if model is UserModel else fake_plan

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    async def _dep():
        return ADMIN

    from app.routers.admin import get_session as admin_get_session

    async def _sess():
        return FakeSession()

    app.dependency_overrides[require_user] = _dep
    app.dependency_overrides[admin_get_session] = _sess
    c = TestClient(app, raise_server_exceptions=False)

    r = c.patch("/api/admin/users/2", json={"plan_id": 3})
    assert r.status_code == 200
    assert fake_user.plan_id == 3
    assert fake_user.plan_expiry_notified_at is None  # 플랜 변경 → D-7 가드 리셋
