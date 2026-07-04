"""그룹 소유권 접근 제어. 타인 그룹은 존재 은닉을 위해 404."""

import pytest
from fastapi.testclient import TestClient

from app.control_db import get_session
from app.main import app
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import can_access_group
from app.services.auth_service import set_users_exist
from tests.test_auth import FakeSession, override_session

ALICE = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="user")
BOB = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")
ADMIN = CurrentUser(user_id=9, email="adm@x.com", display_name="Adm", role="admin")


def test_can_access_group_rules():
    assert can_access_group(1, ALICE) is True     # 본인 소유
    assert can_access_group(1, BOB) is False      # 타인 소유
    assert can_access_group(1, ADMIN) is True     # admin은 전부
    assert can_access_group(None, ALICE) is False # 소유자 미지정(레거시) → admin만
    assert can_access_group(None, ADMIN) is True


def make_group(owner: int | None) -> Group:
    from datetime import datetime, timezone

    g = Group()
    g.group_id, g.slug, g.name, g.schema_name = 1, "invest", "투자", "youtube_invest"
    g.is_active, g.owner_user_id, g.description = True, owner, None
    # response_model=GroupOut 직렬화에 필요 (DB server_default가 없는 인메모리 객체).
    g.created_at = g.updated_at = datetime.now(timezone.utc)
    return g


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as(user: CurrentUser):
    async def _dep():
        return user
    app.dependency_overrides[require_user] = _dep


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_owner_group_404_for_stranger():
    _as(BOB)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 404


def test_owner_group_ok_for_owner():
    _as(ALICE)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 200


def test_owner_group_ok_for_admin():
    _as(ADMIN)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 200
