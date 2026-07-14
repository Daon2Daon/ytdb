"""80%/100% 게이트 판정 + 시스템 키 폴백 거부 (스펙 §1.4)."""

from types import SimpleNamespace

import pytest

from app.services import yt_quota_service as yq


def test_gate_state_boundaries():
    # limit=10000: 7999=ok, 8000=soft(80%), 10000=hard(100%)
    assert yq.gate_state(7999, 10000) == yq.GATE_OK
    assert yq.gate_state(8000, 10000) == yq.GATE_SOFT
    assert yq.gate_state(9999, 10000) == yq.GATE_SOFT
    assert yq.gate_state(10000, 10000) == yq.GATE_HARD
    assert yq.gate_state(15000, 10000) == yq.GATE_HARD


async def test_system_gate_state_no_key_is_ok(monkeypatch):
    async def no_key():
        return ""

    monkeypatch.setattr(yq, "get_system_youtube_key", no_key)
    state, used, limit = await yq.system_gate_state()
    assert state == yq.GATE_OK


async def test_system_gate_state_reads_usage(monkeypatch):
    async def key():
        return "AIza-sys"

    class FakeResult:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

    class FakeSession:
        # 구현의 호출 순서: ①get_youtube_daily_quota(행 없음→기본 10000) ②units_today(8500)
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            return FakeResult(None) if self.calls == 1 else FakeResult(8500)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(yq, "get_system_youtube_key", key)
    monkeypatch.setattr(yq, "get_sessionmaker", lambda: FakeSession)
    state, used, limit = await yq.system_gate_state()
    assert (state, used, limit) == (yq.GATE_SOFT, 8500, 10000)


async def test_resolve_youtube_key_hard_gate_blocks_fallback(monkeypatch):
    from app.services import global_settings as gs
    from app.services.youtube_api import YouTubeQuotaExceededError

    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")  # 그룹 키 없음 → 폴백 경로

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def hard():
        return True

    monkeypatch.setattr(yq, "system_hard_blocked", hard)
    with pytest.raises(YouTubeQuotaExceededError):
        await gs.resolve_youtube_key(1)


async def test_resolve_youtube_key_group_key_unaffected_by_hard_gate(monkeypatch):
    from app.services import global_settings as gs

    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="group-key")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def hard():
        raise AssertionError("그룹 키가 있으면 게이트를 조회조차 안 한다")

    monkeypatch.setattr(yq, "system_hard_blocked", hard)
    assert await gs.resolve_youtube_key(1) == "group-key"


def test_channels_router_maps_quota_error_to_400(monkeypatch):
    """resolve_youtube_key가 쿼터 소진을 던지면 500이 아니라 400."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import channels as ch
    from app.routers.auth import CurrentUser, require_user
    from app.routers.deps import get_group_or_404
    from app.services.auth_service import set_users_exist
    from app.services.youtube_api import YouTubeQuotaExceededError

    set_users_exist(True)
    try:
        async def _u():
            return CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")

        async def _g():
            # owner_user_id=None → limits_for_group_owner가 None(무제한)을 반환해
            # resolve_youtube_key 이전의 쿼터 검사 블록을 DB 없이 우회한다.
            return SimpleNamespace(
                group_id=1, slug="g", schema_name="s", owner_user_id=None
            )

        app.dependency_overrides[require_user] = _u
        app.dependency_overrides[get_group_or_404] = _g

        # get_polling(line 63)이 resolve보다 먼저 실행되므로 DB 접근을 막는다.
        async def _polling(group_id):
            return SimpleNamespace(youtube_api_key="")

        monkeypatch.setattr(
            ch, "get_settings_manager",
            lambda: SimpleNamespace(get_polling=_polling),
        )

        async def boom(group_id):
            raise YouTubeQuotaExceededError("시스템 키 소진")

        monkeypatch.setattr(ch, "resolve_youtube_key", boom)

        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/api/groups/g/channels", json={"channel_input": "@x"})
        assert resp.status_code == 400
        assert "쿼터" in resp.json()["detail"]
    finally:
        set_users_exist(False)
        app.dependency_overrides.clear()
