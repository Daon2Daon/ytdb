"""댓글 수집 테스트 — httpx.MockTransport로 YouTube API를 대체한다."""

import httpx
import pytest

from app.services.settings_types import PollingSettings
from app.services.youtube_api import (
    CommentsDisabledError,
    YouTubeAPIClient,
    YouTubeAPIError,
)


def _polling() -> PollingSettings:
    return PollingSettings(youtube_api_key="test-key", youtube_daily_quota=10000)


def _thread_item(idx: int) -> dict:
    return {
        "id": f"c{idx}",
        "snippet": {
            "topLevelComment": {
                "snippet": {
                    "authorDisplayName": f"user{idx}",
                    "textDisplay": f"comment {idx}",
                    "likeCount": idx,
                    "publishedAt": "2026-07-20T10:00:00Z",
                }
            }
        },
    }


def _client_with(pages: list[dict]) -> YouTubeAPIClient:
    """pages를 순서대로 돌려주는 mock transport 클라이언트."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    transport = httpx.MockTransport(handler)
    return YouTubeAPIClient(_polling(), client=httpx.AsyncClient(transport=transport))


async def test_stops_exactly_at_limit_across_pages():
    # 100건씩 2페이지가 있어도 limit=150이면 정확히 150건에서 멈춘다.
    page1 = {"items": [_thread_item(i) for i in range(100)], "nextPageToken": "tok"}
    page2 = {"items": [_thread_item(i) for i in range(100, 200)]}
    api = _client_with([page1, page2])
    try:
        out = await api.list_comment_threads("vid", limit=150)
    finally:
        await api.aclose()
    assert len(out) == 150
    assert out[0].author == "user0"
    assert out[149].text == "comment 149"


async def test_stops_when_no_next_page_token():
    page1 = {"items": [_thread_item(i) for i in range(30)]}
    api = _client_with([page1])
    try:
        out = await api.list_comment_threads("vid", limit=1000)
    finally:
        await api.aclose()
    assert len(out) == 30


async def test_maps_fields():
    api = _client_with([{"items": [_thread_item(7)]}])
    try:
        out = await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()
    c = out[0]
    assert c.comment_id == "c7"
    assert c.author == "user7"
    assert c.text == "comment 7"
    assert c.like_count == 7
    assert c.published_at == "2026-07-20T10:00:00Z"


async def test_comments_disabled_raises_specific_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "commentsDisabled"}]}},
        )

    api = YouTubeAPIClient(
        _polling(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(CommentsDisabledError):
            await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()


async def test_other_http_error_raises_generic():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    api = YouTubeAPIClient(
        _polling(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(YouTubeAPIError):
            await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()


async def test_quota_recorder_called_once_per_page():
    recorded: list[int] = []

    async def rec(units: int) -> None:
        recorded.append(units)

    page1 = {"items": [_thread_item(i) for i in range(100)], "nextPageToken": "tok"}
    page2 = {"items": [_thread_item(i) for i in range(100, 150)]}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = [page1, page2][calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    api = YouTubeAPIClient(
        _polling(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        recorder=rec,
    )
    try:
        await api.list_comment_threads("vid", limit=1000)
    finally:
        await api.aclose()
    assert recorded == [1, 1]
