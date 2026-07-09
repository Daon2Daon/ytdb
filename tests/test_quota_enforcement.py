"""쿼터 강제 지점 라우터 테스트 — quota_service를 monkeypatch로 치환."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist
from app.services.quota_service import QuotaExceeded
from app.routers.deps import get_group_or_404

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


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


def _as_group():
    async def _dep():
        return _FakeGroup()
    app.dependency_overrides[get_group_or_404] = _dep


def test_add_channel_quota_exceeded_400(monkeypatch):
    _as_user()
    _as_group()

    async def _limits(group):
        from app.services.quota_service import EffectiveLimits
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _deny(session, user_id):
        raise QuotaExceeded("채널 한도 초과: 현재 5개 / 한도 5개", limit=5, current=5)

    monkeypatch.setattr("app.routers.channels.limits_for_group_owner", _limits)
    monkeypatch.setattr("app.routers.channels.check_channel_quota", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups/g1/channels", json={"channel_input": "@x"})
    assert resp.status_code == 400
    assert "채널 한도 초과" in resp.json()["detail"]


def test_add_channel_poll_interval_below_plan_floor_400(monkeypatch):
    _as_user()
    _as_group()

    async def _limits(group):
        from app.services.quota_service import EffectiveLimits
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _ok(session, user_id):
        return None

    monkeypatch.setattr("app.routers.channels.limits_for_group_owner", _limits)
    monkeypatch.setattr("app.routers.channels.check_channel_quota", _ok)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/groups/g1/channels",
        json={"channel_input": "@x", "poll_interval_min": 30},
    )
    assert resp.status_code == 400
    assert "폴링 주기" in resp.json()["detail"]
