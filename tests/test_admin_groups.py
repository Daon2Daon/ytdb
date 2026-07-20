"""관리자 그룹 관리 분리 — 일반 목록은 admin도 본인 소유만, 전체 조회는 /api/admin/groups."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


# ── GET /api/groups: admin도 본인 소유만 (사이드바 오염·오조작 방지) ─────────


def test_list_groups_stmt_filters_by_owner_for_admin():
    from app.routers.groups import owned_groups_stmt

    sql = str(owned_groups_stmt(ADMIN))
    assert "owner_user_id" in sql


def test_list_groups_stmt_filters_by_owner_for_user():
    from app.routers.groups import owned_groups_stmt

    sql = str(owned_groups_stmt(USER))
    assert "owner_user_id" in sql


# ── GET /api/admin/groups: 전체 그룹 + 소유자 이메일 ─────────────────────────


def test_admin_groups_route_registered():
    assert "/api/admin/groups" in {r.path for r in app.routes}


def test_admin_groups_non_admin_403():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/groups").status_code == 403


def test_admin_groups_response_shape(monkeypatch):
    from app.schemas.admin import AdminGroupOut

    async def _a():
        return ADMIN
    app.dependency_overrides[require_user] = _a

    async def _fake_list(session):
        return [
            AdminGroupOut(
                group_id=27, slug="u2_207eb5", name="지식채널",
                schema_name="youtube_u2_207eb5", is_active=True,
                owner_user_id=2, owner_email="u@x.com",
            )
        ]

    monkeypatch.setattr("app.routers.admin.list_all_groups_with_owner", _fake_list)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/admin/groups")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["slug"] == "u2_207eb5"
    assert body[0]["owner_email"] == "u@x.com"
