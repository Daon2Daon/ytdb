"""프리셋 관리자 API: 라우트 등록 + 비관리자 403 + 불변성(본문 PATCH 불가)."""

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


def test_preset_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/presets" in paths
    assert "/api/admin/presets/{preset_id}" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/presets").status_code == 403
    assert c.post("/api/admin/presets", json={}).status_code == 403


def test_patch_schema_rejects_prompt_body_changes():
    """PresetPatch에 analysis_prompt/digest_prompt 필드가 없어야 한다(불변성)."""
    from app.schemas.admin import PresetPatch

    fields = set(PresetPatch.model_fields.keys())
    assert fields == {"name", "description", "is_active"}
