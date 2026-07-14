"""YouTubeAPIClient recorder 주입 — 시도마다 유닛 기록, 미주입 시 무변경."""

import httpx
import pytest

from app.services.settings_types import PollingSettings
from app.services.youtube_api import YouTubeAPIClient, YouTubeAPIError


def _client(status=200, payload=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload if payload is not None else {"items": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _polling():
    return PollingSettings(youtube_api_key="AIza-test")


async def test_recorder_called_with_units_on_success():
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(_polling(), client=_client(), recorder=rec)
    await api._get("videos", {"part": "id"}, 1)
    assert recorded == [1]


async def test_recorder_called_even_on_http_error():
    # Google은 실패 호출도 과금 — 시도 기준 기록 (스펙 §1.2)
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(_polling(), client=_client(status=500), recorder=rec)
    with pytest.raises(YouTubeAPIError):
        await api._get("videos", {"part": "id"}, 1)
    assert recorded == [1]


async def test_search_records_100_units():
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(
        _polling(),
        client=_client(payload={"items": [{"snippet": {"channelId": "UCx"}}]}),
        recorder=rec,
    )
    await api._resolve_by_search("some channel")
    assert recorded == [100]


async def test_no_recorder_keeps_existing_behavior():
    api = YouTubeAPIClient(_polling(), client=_client())
    data = await api._get("videos", {"part": "id"}, 1)
    assert data == {"items": []}
