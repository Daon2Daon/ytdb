"""그룹별 텔레그램 알림 발송.

분석 완료 영상을 그룹의 봇 토큰으로 그룹에 설정된 모든 chat_id에 발송한다.
chat_id가 없거나 비활성이면 발송하지 않는다(분석/데이터만 기록).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from html import escape
from typing import Callable, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.channel import Channel
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.job_logger import (
    JOB_TYPE_NOTIFY,
    STATUS_FAIL,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.settings_types import NotificationSettings

MakeSession = Callable[[], AsyncSession]

_TELEGRAM_API = "https://api.telegram.org"
_MAX_LEN = 3900  # 텔레그램 4096자 제한 여유
_TELEGRAM_MAX_LEN = 4096


def _to_kst(dt) -> str:
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return str(dt)


def _format_duration(seconds) -> str:
    if not seconds:
        return ""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_bullets(bullet_points) -> str:
    if not isinstance(bullet_points, list):
        return ""
    out = []
    for b in bullet_points:
        if b is None:
            continue
        s = str(b).strip()
        if s:
            out.append(f"• {escape(s)}")
    return "\n".join(out)


def _truncate_html(text: str, max_len: int, suffix: str = "...") -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def _build_compact(video, analysis, threshold: float) -> str:
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
    return "\n".join(lines)[:_TELEGRAM_MAX_LEN]


def _render_full(*, low_conf, channel_name, headline, body, bullets_list, tags, meta_parts, url) -> str:
    lines = []
    if low_conf:
        lines.append("⚠️ <b>[저신뢰도 분석]</b>")
        lines.append("")
    if channel_name:
        lines.append(f"<b>🎬 [{escape(channel_name)}] 신규 영상</b>")
        lines.append("")
    if headline:
        lines.append(f"<b>{escape(headline)}</b>")
        lines.append("")
    if body:
        lines.append(escape(body))
        lines.append("")
    bullets = _format_bullets(bullets_list)
    if bullets:
        lines.append(bullets)
        lines.append("")
    if tags:
        lines.append("🏷 " + ", ".join(escape(t) for t in tags))
    if meta_parts:
        lines.append("  ·  ".join(meta_parts))
    lines.append("")
    if url:
        lines.append(f'🔗 <a href="{escape(url, quote=True)}">영상 보러가기</a>')
    return "\n".join(lines)


def _build_full(video, analysis, threshold: float, channel_name: str, tags) -> str:
    low_conf = (
        analysis.confidence_score is not None
        and float(analysis.confidence_score) < float(threshold)
    )
    headline = analysis.headline or video.title or ""
    body = analysis.full_analysis_md or analysis.short_summary_md or ""
    bullets_list = analysis.bullet_points if isinstance(analysis.bullet_points, list) else []
    meta_parts = []
    if video.published_at:
        meta_parts.append(f"📅 {_to_kst(video.published_at)}")
    dur = _format_duration(video.duration_seconds)
    if dur:
        meta_parts.append(f"⏱ {dur}")

    def render(b, bl):
        return _render_full(
            low_conf=low_conf, channel_name=channel_name, headline=headline,
            body=b, bullets_list=bl, tags=tags, meta_parts=meta_parts, url=video.video_url,
        )

    text = render(body, bullets_list)
    if len(text) <= _TELEGRAM_MAX_LEN:
        return text
    overflow = len(text) - _TELEGRAM_MAX_LEN + 50
    if len(body) > overflow:
        return render(body[: len(body) - overflow] + "…", bullets_list)
    if bullets_list:
        return render("", bullets_list[:-1])
    return _truncate_html(text, _TELEGRAM_MAX_LEN)


def build_message(video, analysis, threshold: float = 0.0, *,
                  channel_name: str = "", tags=None, detail: str = "full") -> str:
    if detail == "compact":
        return _build_compact(video, analysis, threshold)
    return _build_full(video, analysis, threshold, channel_name, tags or [])


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


async def notify_pending_batch(
    notif: NotificationSettings,
    make_session: MakeSession,
    *,
    max_per: int,
    wait_sec: int,
    threshold: float,
    log_label: str,
) -> int:
    """미발송·분석완료 영상을 오래된 순으로 배치 발송한다.

    대상: analysis_status='done' AND notified_at IS NULL AND 채널 notify_enabled
          AND baseline(notify_from) 통과. 최대 max_per건, 건당 wait_sec 대기.
    각 성공 건은 notified_at을 기록한다. 배치 종료 후 job_log 1건 기록.
    반환: 성공 발송 건수.
    """
    from app.services.monitor_service import _passes_notify_baseline

    max_per = max(1, min(50, int(max_per)))

    async with make_session() as sess:
        rows = (
            await sess.execute(
                select(Video, VideoAnalysis, Channel)
                .join(VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk)
                .join(Channel, Channel.channel_pk == Video.channel_pk)
                .where(Video.analysis_status == "done")
                .where(Video.notified_at.is_(None))
                .where(Channel.notify_enabled.is_(True))
                .order_by(Video.published_at.asc())
            )
        ).all()

    candidates = [
        (v, a)
        for (v, a, ch) in rows
        if _passes_notify_baseline(ch.notify_from, v.published_at)
    ]
    if not candidates:
        return 0

    batch = candidates[:max_per]
    remaining = len(candidates) - len(batch)
    timer = JobTimer()
    sent = 0
    try:
        with timer:
            client = httpx.AsyncClient(timeout=20.0)
            try:
                for i, (video, analysis) in enumerate(batch):
                    try:
                        ok = await notify_video(notif, video, analysis, client, threshold)
                        if ok:
                            async with make_session() as sess:
                                async with sess.begin():
                                    await sess.execute(
                                        update(Video)
                                        .where(Video.video_pk == video.video_pk)
                                        .values(notified_at=datetime.now(timezone.utc))
                                    )
                            sent += 1
                    except Exception as exc:
                        print(f"⚠️ {log_label}: video_pk={video.video_pk} 발송 실패 — {exc}")
                    if i < len(batch) - 1 and wait_sec > 0:
                        await asyncio.sleep(wait_sec)
            finally:
                await client.aclose()
    finally:
        msg = f"{log_label}: {sent}/{len(batch)}건 발송" + (
            f", 잔여 약 {remaining}건" if remaining else ""
        )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SUCCESS if sent > 0 else STATUS_FAIL,
            message=msg,
            duration_ms=timer.elapsed_ms,
        )
    return sent


async def run_notify_tick_once() -> None:
    """매 1분 호출. 활성 그룹별로 예약발송/야간 보정 발송을 수행한다.

    - scheduled 모드: 그룹 tz 기준 현재 분이 예약 시각과 일치하고, 야간 제한 중이
      아니면 배치 발송.
    - immediate 모드 + 야간 제한 활성: 야간이 끝난 뒤(현재 비-야간) 보류분을 보정 발송.
    """
    from zoneinfo import ZoneInfo

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.services.db_engine import (
        DBNotConfiguredError,
        data_plane_engine_manager as dpm,
    )
    from app.services.quiet_hours import is_quiet_hours_now
    from app.services.settings_manager import get_settings_manager

    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True))))
            .scalars()
            .all()
        )

    for group in groups:
        try:
            notif = await mgr.get_notification(group.group_id)
            if not notif.is_sendable:
                continue
            try:
                tz = ZoneInfo(notif.timezone)
            except Exception:
                tz = ZoneInfo("Asia/Seoul")
            now_local = datetime.now(tz)
            quiet_now = is_quiet_hours_now(
                notif.quiet_hours_enabled,
                notif.quiet_hours_start,
                notif.quiet_hours_end,
                tz=tz,
                now=now_local,
            )

            if notif.send_mode == "scheduled":
                if quiet_now:
                    continue
                if not _matches_scheduled_time(now_local, notif.scheduled_times):
                    continue
                log_label = "예약발송 회차"
            elif notif.send_mode == "immediate":
                if not notif.quiet_hours_enabled or quiet_now:
                    continue
                log_label = "야간 보정 발송"
            else:
                continue

            try:
                engine = await dpm.get_engine_for_group(group)
            except DBNotConfiguredError:
                continue
            make_session = lambda: dpm.session_for_group(engine, group.schema_name)
            await notify_pending_batch(
                notif,
                make_session,
                max_per=notif.scheduled_max_per_run,
                wait_sec=notif.wait_between_messages_sec,
                threshold=notif.low_confidence_threshold,
                log_label=log_label,
            )
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] notify tick 실패: {e}")
