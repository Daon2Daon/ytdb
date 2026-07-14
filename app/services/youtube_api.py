"""YouTube Data API v3 래퍼 (그룹별 PollingSettings의 API 키 사용).

- 입력값(URL/@handle/UC id)을 channel_id로 정규화 후 메타/업로드 플레이리스트 조회
- 업로드 플레이리스트 최근 영상 조회, videos.list 상세 일괄 조회
- 쿼터: 인스턴스 메모리 가드(2차 방어) + 선택적 recorder로 영속 기록(yt_quota_service)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import httpx

from app.services.settings_types import PollingSettings


class YouTubeAPIError(RuntimeError):
    pass


class YouTubeQuotaExceededError(YouTubeAPIError):
    pass


@dataclass(frozen=True)
class ChannelMeta:
    channel_id: str
    channel_name: str
    upload_playlist_id: str
    channel_handle: str | None = None
    thumbnail_url: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class PlaylistItemMeta:
    video_id: str
    published_at: str | None
    title: str | None


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    video_url: str
    title: str
    description: str | None
    thumbnail_url: str | None
    published_at: str
    duration: str | None
    view_count: int | None
    like_count: int | None
    channel_id: str | None = None
    channel_title: str | None = None


_UC_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{20,}$")
_HANDLE_RE = re.compile(r"^@[\w.-]{3,}$")


def _extract_from_url(input_str: str) -> Tuple[str, str | None]:
    p = urlparse(input_str)
    path = (p.path or "").strip("/")
    if not path:
        raise YouTubeAPIError("채널 URL이 유효하지 않습니다.")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "channel":
        return "channel_id", parts[1]
    if parts[0].startswith("@"):
        return "handle", parts[0]
    if len(parts) >= 2 and parts[0] == "user":
        return "username", parts[1]
    if len(parts) >= 2 and parts[0] == "c":
        return "custom", parts[1]
    return "custom", parts[-1]


def _first_thumb(snippet: Dict[str, Any]) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for k in ("high", "medium", "default"):
        if k in thumbs and thumbs[k].get("url"):
            return thumbs[k]["url"]
    return None


class YouTubeAPIClient:
    def __init__(
        self,
        polling: PollingSettings,
        client: httpx.AsyncClient | None = None,
        recorder: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        if not polling.youtube_api_key:
            raise YouTubeAPIError("YouTube API 키가 없습니다. (polling.youtube_api_key)")
        self._polling = polling
        self._api_key = polling.youtube_api_key
        self._base_url = "https://www.googleapis.com/youtube/v3"
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._quota_day: date | None = None
        self._quota_used = 0
        self._recorder = recorder

    async def aclose(self) -> None:
        await self._client.aclose()

    def _consume_quota(self, units: int) -> None:
        today = date.today()
        if self._quota_day != today:
            self._quota_day = today
            self._quota_used = 0
        self._quota_used += units
        if self._quota_used > int(self._polling.youtube_daily_quota or 10000):
            raise YouTubeQuotaExceededError(
                f"YouTube API 쿼터 초과: used={self._quota_used}, "
                f"limit={self._polling.youtube_daily_quota}"
            )

    async def _get(self, path: str, params: Dict[str, Any], quota_units: int) -> Dict[str, Any]:
        self._consume_quota(quota_units)
        if self._recorder is not None:
            await self._recorder(quota_units)
        resp = await self._client.get(
            f"{self._base_url}/{path.lstrip('/')}", params={**params, "key": self._api_key}
        )
        if resp.status_code != 200:
            raise YouTubeAPIError(f"YouTube API 오류: {resp.status_code} - {resp.text}")
        return resp.json()

    async def resolve_channel(self, input_str: str) -> ChannelMeta:
        s = (input_str or "").strip()
        if not s:
            raise YouTubeAPIError("채널 입력값이 비어 있습니다.")
        if _UC_ID_RE.match(s):
            channel_id = s
        elif _HANDLE_RE.match(s):
            channel_id = await self._resolve_by_handle(s)
        elif s.startswith(("http://", "https://")):
            kind, value = _extract_from_url(s)
            if kind == "channel_id":
                channel_id = value or ""
            elif kind == "handle":
                channel_id = await self._resolve_by_handle(value or "")
            elif kind == "username":
                channel_id = await self._resolve_by_username(value or "")
            else:
                channel_id = await self._resolve_by_search(value or "")
        elif s.startswith("@"):
            channel_id = await self._resolve_by_handle(s)
        else:
            channel_id = await self._resolve_by_search(s)
        return await self.get_channel_meta(channel_id)

    async def _resolve_by_handle(self, handle: str) -> str:
        if not handle.startswith("@"):
            handle = "@" + handle
        data = await self._get("channels", {"part": "id", "forHandle": handle}, 1)
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"handle로 채널을 찾을 수 없습니다: {handle}")
        return items[0]["id"]

    async def _resolve_by_username(self, username: str) -> str:
        data = await self._get("channels", {"part": "id", "forUsername": username}, 1)
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"username으로 채널을 찾을 수 없습니다: {username}")
        return items[0]["id"]

    async def _resolve_by_search(self, q: str) -> str:
        data = await self._get(
            "search", {"part": "snippet", "q": q, "type": "channel", "maxResults": 1}, 100
        )
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"search로 채널을 찾을 수 없습니다: {q}")
        return items[0]["snippet"]["channelId"]

    async def get_channel_meta(self, channel_id: str) -> ChannelMeta:
        data = await self._get(
            "channels", {"part": "snippet,contentDetails", "id": channel_id}, 1
        )
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"채널 메타를 찾을 수 없습니다: {channel_id}")
        it = items[0]
        snippet = it.get("snippet") or {}
        related = (it.get("contentDetails") or {}).get("relatedPlaylists") or {}
        uploads = related.get("uploads")
        if not uploads:
            raise YouTubeAPIError("업로드 플레이리스트 ID를 찾을 수 없습니다.")
        return ChannelMeta(
            channel_id=channel_id,
            channel_name=snippet.get("title") or channel_id,
            upload_playlist_id=uploads,
            channel_handle=snippet.get("customUrl"),
            thumbnail_url=_first_thumb(snippet),
            description=snippet.get("description"),
        )

    async def get_latest_playlist_items(
        self,
        playlist_id: str,
        max_results: int = 5,
        published_after: datetime | None = None,
    ) -> List[PlaylistItemMeta]:
        """업로드 플레이리스트의 최신 영상 목록.

        published_after가 주어지면 해당 시점 이후 영상을 모두 수집하기 위해
        페이지를 넘기며 조회한다(업로드 목록은 최신순이라 컷오프보다 오래된
        항목을 만나면 중단). published_after가 없으면 단일 페이지에서
        max_results개만 조회한다.
        """
        MAX_PAGES = 20  # 안전 상한 (페이지당 50개 → 최대 1000개)
        out: List[PlaylistItemMeta] = []
        page_token: str | None = None
        pages = 0
        while True:
            params: Dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50 if published_after else max_results,
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._get("playlistItems", params, 1)
            reached_cutoff = False
            for it in data.get("items") or []:
                vid = (it.get("contentDetails") or {}).get("videoId")
                if not vid:
                    continue
                s = it.get("snippet") or {}
                published = s.get("publishedAt")
                if published_after is not None and published:
                    try:
                        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        if pub_dt < published_after:
                            reached_cutoff = True
                            break
                    except ValueError:
                        pass
                out.append(
                    PlaylistItemMeta(video_id=vid, published_at=published, title=s.get("title"))
                )
            page_token = data.get("nextPageToken")
            pages += 1
            if published_after is None or reached_cutoff or not page_token or pages >= MAX_PAGES:
                break
        return out

    async def get_video_details(self, video_ids: Iterable[str]) -> List[VideoMeta]:
        ids = [v for v in video_ids if v]
        if not ids:
            return []

        def to_int(x: Any) -> int | None:
            try:
                return int(x)
            except (TypeError, ValueError):
                return None

        out: List[VideoMeta] = []
        # videos.list는 요청당 최대 50개 ID만 허용하므로 50개씩 배치 조회한다.
        for start in range(0, len(ids), 50):
            batch = ids[start : start + 50]
            data = await self._get(
                "videos",
                {
                    "part": "snippet,contentDetails,statistics",
                    "id": ",".join(batch),
                    "maxResults": len(batch),
                },
                1,
            )
            for it in data.get("items") or []:
                vid = it.get("id")
                snippet = it.get("snippet") or {}
                cdetails = it.get("contentDetails") or {}
                stats = it.get("statistics") or {}
                out.append(
                    VideoMeta(
                        video_id=vid,
                        video_url=f"https://www.youtube.com/watch?v={vid}",
                        title=snippet.get("title") or "",
                        description=snippet.get("description"),
                        thumbnail_url=_first_thumb(snippet),
                        published_at=snippet.get("publishedAt") or "",
                        duration=cdetails.get("duration"),
                        view_count=to_int(stats.get("viewCount")),
                        like_count=to_int(stats.get("likeCount")),
                        channel_id=snippet.get("channelId"),
                        channel_title=snippet.get("channelTitle"),
                    )
                )
        return out
