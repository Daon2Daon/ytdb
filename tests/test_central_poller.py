"""중앙 폴링 팬아웃 검증 — 채널당 API 조회 1회, 그룹 실패 격리, 쿼터 중단.

모듈 경계(fetch/팬아웃/mark)를 monkeypatch해 오케스트레이션만 검증한다.
실제 SQL·API는 실 DB E2E에서.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import central_poller as cp
from app.services.channel_registry_service import DueChannel
from app.services.youtube_api import YouTubeQuotaExceededError

DUE = [
    DueChannel("UC1", "UU1", effective_interval_min=60, fetch_window_hours=24),
    DueChannel("UC2", "UU2", effective_interval_min=60, fetch_window_hours=48),
]
GROUP_A = SimpleNamespace(group_id=1, slug="a", schema_name="youtube_a")
GROUP_B = SimpleNamespace(group_id=2, slug="b", schema_name="youtube_b")


@pytest.fixture
def wired(monkeypatch):
    """공통 배선: 시스템 키/플로어/due/구독/그룹 + 기록용 스파이."""
    calls = SimpleNamespace(fetched=[], fanned=[], marked=[])

    async def fake_system_key():
        return "sys-key"

    async def fake_prepare():
        subs = {
            "UC1": [SimpleNamespace(group_id=1, window_hours=24),
                    SimpleNamespace(group_id=2, window_hours=24)],
            "UC2": [SimpleNamespace(group_id=1, window_hours=48)],
        }
        return DUE, subs, {1: GROUP_A, 2: GROUP_B}

    async def fake_fetch(api, playlist_id, cutoff):
        calls.fetched.append(playlist_id)
        return [SimpleNamespace(video_id="v1", published_at="2026-07-05T00:00:00Z")]

    async def fake_fan_out(group, channel_id, metas, window_hours, now):
        calls.fanned.append((group.slug, channel_id))
        return 1

    async def fake_mark(channel_id, now, last_video_at):
        calls.marked.append(channel_id)

    monkeypatch.setattr(cp, "get_system_youtube_key", fake_system_key)
    monkeypatch.setattr(cp, "_prepare_tick", fake_prepare)
    monkeypatch.setattr(cp, "fetch_channel_updates", fake_fetch)
    monkeypatch.setattr(cp, "_fan_out_group", fake_fan_out)
    monkeypatch.setattr(cp, "_mark_polled", fake_mark)
    return calls


async def test_one_fetch_per_channel_fanout_all_groups(wired):
    await cp.run_central_poll_once()
    # 채널 2개 → API 조회 2회 (그룹 3구독이어도 3회 아님)
    assert sorted(wired.fetched) == ["UU1", "UU2"]
    # UC1은 두 그룹, UC2는 한 그룹에 팬아웃
    assert sorted(wired.fanned) == [("a", "UC1"), ("a", "UC2"), ("b", "UC1")]
    assert sorted(wired.marked) == ["UC1", "UC2"]


async def test_group_failure_isolated(wired, monkeypatch):
    async def failing_fan_out(group, channel_id, metas, window_hours, now):
        if group.slug == "a":
            raise RuntimeError("boom")
        wired.fanned.append((group.slug, channel_id))
        return 1

    monkeypatch.setattr(cp, "_fan_out_group", failing_fan_out)
    await cp.run_central_poll_once()  # 예외 전파 없음
    assert ("b", "UC1") in wired.fanned          # 다른 그룹은 계속
    assert sorted(wired.marked) == ["UC1", "UC2"]  # 채널 자체는 폴링 완료 처리


async def test_quota_exceeded_aborts_tick(wired, monkeypatch):
    async def quota_fetch(api, playlist_id, cutoff):
        wired.fetched.append(playlist_id)
        raise YouTubeQuotaExceededError("quota")

    monkeypatch.setattr(cp, "fetch_channel_updates", quota_fetch)
    await cp.run_central_poll_once()  # 예외 전파 없음
    assert wired.fanned == []
    assert wired.marked == []  # 폴링 실패 → 다음 틱 재시도 (idempotent, 스펙 §8)


async def test_no_system_key_skips(wired, monkeypatch):
    async def no_key():
        return ""

    monkeypatch.setattr(cp, "get_system_youtube_key", no_key)
    await cp.run_central_poll_once()
    assert wired.fetched == []
