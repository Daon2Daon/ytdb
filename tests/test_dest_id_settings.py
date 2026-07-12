"""notification dest_id 설정 로드·PUT 검증 (설계 §5)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.auth_service import set_users_exist
from app.services.settings_types import NotificationSettings

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_notification_settings_has_dest_id_default_none():
    assert NotificationSettings().dest_id is None


def test_put_dest_id_invalid_ownership_400(monkeypatch):
    async def _u():
        return USER
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g

    async def _not_owned(dest_id, owner_user_id):
        return False

    monkeypatch.setattr("app.routers.settings._dest_owned_and_active", _not_owned)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.put(
        "/api/groups/g1/settings/notification",
        json={"items": [{"key": "dest_id", "value": "77", "value_type": "int"}]},
    )
    assert resp.status_code == 400
    assert "텔레그램 연결" in resp.json()["detail"]


def test_put_dest_id_owner_null_400():
    class _LegacyGroup:
        group_id = 11
        slug = "g1"
        owner_user_id = None
        schema_name = "youtube_g1"

    async def _u():
        return USER
    async def _g():
        return _LegacyGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.put(
        "/api/groups/g1/settings/notification",
        json={"items": [{"key": "dest_id", "value": "5", "value_type": "int"}]},
    )
    assert resp.status_code == 400
    assert "직접 봇 설정" in resp.json()["detail"]


def test_put_dest_id_clear_skips_validation(monkeypatch):
    async def _u():
        return USER
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g

    async def _boom(dest_id, owner_user_id):
        raise AssertionError("클리어 값은 검증을 타면 안 됨")

    monkeypatch.setattr("app.routers.settings._dest_owned_and_active", _boom)

    async def _fake_list(group_id, category):
        return []

    async def _fake_set(group_id, category, items):
        return None

    class _Mgr:
        list_for_api = staticmethod(_fake_list)
        set_values = staticmethod(_fake_set)

        async def get_notification(self, group_id):
            return NotificationSettings()

    monkeypatch.setattr("app.routers.settings.get_settings_manager", lambda: _Mgr())

    async def _rnt(owner_user_id, notif):
        return notif

    monkeypatch.setattr("app.routers.settings.resolve_notify_target", _rnt)

    c = TestClient(app, raise_server_exceptions=False)
    for clear_value in ("", "0"):
        resp = c.put(
            "/api/groups/g1/settings/notification",
            json={"items": [{"key": "dest_id", "value": clear_value, "value_type": "int"}]},
        )
        assert resp.status_code == 200, clear_value
