"""그룹별 텔레그램 알림 발송.

분석 완료 영상을 그룹의 봇 토큰으로 그룹에 설정된 모든 chat_id에 발송한다.
chat_id가 없거나 비활성이면 발송하지 않는다(분석/데이터만 기록).
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional

import httpx

from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.settings_types import NotificationSettings

_TELEGRAM_API = "https://api.telegram.org"
_MAX_LEN = 3900  # 텔레그램 4096자 제한 여유


def build_message(video: Video, analysis: VideoAnalysis, threshold: float = 0.0) -> str:
    title = analysis.headline or video.title or ""
    low_conf = (
        analysis.confidence_score is not None
        and float(analysis.confidence_score) < float(threshold)
    )
    badge = "⚠️ " if low_conf else ""
    lines = [f"<b>{badge}{escape(title)}</b>"]
    if analysis.one_line:
        lines.append(escape(analysis.one_line))
    if analysis.short_summary_md:
        lines.append("")
        lines.append(escape(analysis.short_summary_md))
    meta = []
    if analysis.sentiment:
        meta.append(f"감성: {escape(analysis.sentiment)}")
    if analysis.confidence_score is not None:
        meta.append(f"신뢰도: {analysis.confidence_score:.2f}")
    if meta:
        lines.append("")
        lines.append(" | ".join(meta))
    if video.video_url:
        lines.append("")
        lines.append(escape(video.video_url))
    text = "\n".join(lines)
    return text[:_MAX_LEN]


def _matches_scheduled_time(now_local: datetime, scheduled_times: list[str]) -> bool:
    """now_local의 HH:MM이 예약 시각 목록 중 하나와 분 단위로 일치하는지."""
    cur = f"{now_local.hour:02d}:{now_local.minute:02d}"
    valid: set[str] = set()
    for t in scheduled_times:
        parts = str(t).strip().split(":")
        if len(parts) != 2:
            continue
        try:
            h, m = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            valid.add(f"{h:02d}:{m:02d}")
    return cur in valid


async def send_telegram(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
) -> None:
    resp = await client.post(
        f"{_TELEGRAM_API}/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"텔레그램 발송 실패(chat_id={chat_id}): {resp.status_code} - {resp.text}")


async def notify_video(
    notif: NotificationSettings,
    video: Video,
    analysis: VideoAnalysis,
    client: Optional[httpx.AsyncClient] = None,
    threshold: float = 0.0,
) -> int:
    """그룹의 모든 chat_id에 발송. 성공 건수 반환. 일부 실패해도 나머지는 계속 시도."""
    if not notif.is_sendable:
        return 0
    text = build_message(video, analysis, threshold)
    own_client = client is None
    cl = client or httpx.AsyncClient(timeout=20.0)
    sent = 0
    errors: list[str] = []
    try:
        for chat_id in notif.chat_ids:
            try:
                await send_telegram(cl, notif.bot_token, chat_id, text, notif.parse_mode)
                sent += 1
            except Exception as e:
                errors.append(str(e))
    finally:
        if own_client:
            await cl.aclose()
    if errors and sent == 0:
        raise RuntimeError("; ".join(errors)[:500])
    return sent
