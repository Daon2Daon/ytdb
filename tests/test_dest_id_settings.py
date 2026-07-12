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
