"""관리자 API 권한(비관리자 403)과 라우트 등록 검증."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_admin_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users" in paths
    assert "/api/admin/invitations" in paths
    assert "/api/admin/invitations/{invite_id}" in paths
    assert "/api/admin/plans" in paths
    assert "/api/admin/global-settings" in paths          # B-0b


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/invitations").status_code == 403
    assert c.post("/api/admin/invitations", json={}).status_code == 403


def test_non_admin_forbidden_global_settings():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/global-settings").status_code == 403
    assert c.put("/api/admin/global-settings", json={"items": []}).status_code == 403


def test_unauthenticated_401():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 401


class _FakeSession:
    async def commit(self):
        pass


def _patch_globals(monkeypatch, current_secret):
    """admin 라우터의 get/set_global을 가짜로 치환. 저장 호출을 dict로 수집."""
    from app.routers import admin as admin_router

    saved = {}

    async def fake_get_global(session, key):
        return current_secret if key == "youtube_api_key" else None

    async def fake_set_global(session, key, value):
        saved[key] = value

    monkeypatch.setattr(admin_router, "get_global", fake_get_global)
    monkeypatch.setattr(admin_router, "set_global", fake_set_global)
    return saved


async def test_put_global_settings_masked_roundtrip_preserves_secret(monkeypatch):
    """GET의 마스킹 값을 그대로 PUT해도 실제 키가 덮어써지지 않는다 (Critical 회귀)."""
    from app.routers import admin as admin_router
    from app.schemas.admin import GlobalSettingItem, GlobalSettingsUpdate
    from app.services.settings_manager import mask_secret

    real_key = "AIza-real-key-1234"
    saved = _patch_globals(monkeypatch, current_secret=real_key)

    payload = GlobalSettingsUpdate(items=[
        GlobalSettingItem(key="youtube_api_key", value=mask_secret(real_key), is_secret=True),
    ])
    out = await admin_router.put_global_settings(payload, session=_FakeSession())
    assert "youtube_api_key" not in saved  # 마스크 재전송은 무변경
    # 응답도 마스킹된 값을 반환한다
    got = {i.key: i.value for i in out}
    assert got["youtube_api_key"] == mask_secret(real_key)


async def test_put_global_settings_new_secret_saved(monkeypatch):
    """마스크가 아닌 새 값은 정상 저장된다."""
    from app.routers import admin as admin_router
    from app.schemas.admin import GlobalSettingItem, GlobalSettingsUpdate

    saved = _patch_globals(monkeypatch, current_secret="AIza-old-key-9999")

    payload = GlobalSettingsUpdate(items=[
        GlobalSettingItem(key="youtube_api_key", value="AIza-new-key-5678", is_secret=True),
    ])
    await admin_router.put_global_settings(payload, session=_FakeSession())
    assert saved == {"youtube_api_key": "AIza-new-key-5678"}


async def test_put_global_settings_poll_floor_must_be_positive_int(monkeypatch):
    """central_poll_floor_min은 양의 정수만 허용 — 아니면 400."""
    from fastapi import HTTPException

    from app.routers import admin as admin_router
    from app.schemas.admin import GlobalSettingItem, GlobalSettingsUpdate

    saved = _patch_globals(monkeypatch, current_secret=None)

    for bad in ("abc", "0", "-5"):
        payload = GlobalSettingsUpdate(items=[
            GlobalSettingItem(key="central_poll_floor_min", value=bad),
        ])
        with pytest.raises(HTTPException) as exc:
            await admin_router.put_global_settings(payload, session=_FakeSession())
        assert exc.value.status_code == 400
    assert saved == {}

    payload = GlobalSettingsUpdate(items=[
        GlobalSettingItem(key="central_poll_floor_min", value="15"),
    ])
    await admin_router.put_global_settings(payload, session=_FakeSession())
    assert saved == {"central_poll_floor_min": "15"}


def test_global_settings_includes_ai_keys():
    from app.routers.admin import _GLOBAL_KEYS

    assert "ai_base_url" in _GLOBAL_KEYS
    assert "ai_api_key" in _GLOBAL_KEYS
    assert "ai_model_prices" in _GLOBAL_KEYS


def test_global_settings_includes_telegram_bot_token():
    from app.routers.admin import _GLOBAL_KEYS

    assert "telegram_bot_token" in _GLOBAL_KEYS


def test_global_settings_includes_youtube_daily_quota():
    from app.routers import admin as admin_router

    assert "youtube_daily_quota" in admin_router._GLOBAL_KEYS


async def test_put_global_settings_youtube_daily_quota_must_be_positive_int(monkeypatch):
    """youtube_daily_quota는 양의 정수만 허용 — 아니면 400."""
    from fastapi import HTTPException

    from app.routers import admin as admin_router
    from app.schemas.admin import GlobalSettingItem, GlobalSettingsUpdate

    saved = _patch_globals(monkeypatch, current_secret=None)

    for bad in ("abc", "0", "-5"):
        payload = GlobalSettingsUpdate(items=[
            GlobalSettingItem(key="youtube_daily_quota", value=bad),
        ])
        with pytest.raises(HTTPException) as exc:
            await admin_router.put_global_settings(payload, session=_FakeSession())
        assert exc.value.status_code == 400
    assert saved == {}

    payload = GlobalSettingsUpdate(items=[
        GlobalSettingItem(key="youtube_daily_quota", value="50000"),
    ])
    await admin_router.put_global_settings(payload, session=_FakeSession())
    assert saved == {"youtube_daily_quota": "50000"}
