"""그룹별 텔레그램 알림 발송.

분석 완료 영상을 그룹의 봇 토큰으로 그룹에 설정된 모든 chat_id에 발송한다.
chat_id가 없거나 비활성이면 발송하지 않는다(분석/데이터만 기록).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from html import escape
from typing import Callable, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.channel import Channel
from app.models.pg.tag import Tag, VideoTag
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
_TELEGRAM_MAX_LEN = 4096

# 마크다운 → 텔레그램 HTML 변환 시 처리되지 않는 나머지 문자 이스케이프용 패턴
_HTML_CHARS = re.compile(r"[&<>]")
_HTML_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _escape_plain(text: str) -> str:
    """마크다운 변환 후 남은 평문 부분의 HTML 특수문자만 이스케이프."""
    return _HTML_CHARS.sub(lambda m: _HTML_MAP[m.group()], text)


def _md_to_telegram_html(text: str) -> str:
    """full_analysis_md의 마크다운을 텔레그램 HTML로 변환.

    지원: ### ~ # 제목 → <b>, **굵게** → <b>, _기울임_ → <i>, `코드` → <code>.
    나머지 &, <, > 는 이스케이프. 빈 줄·줄바꿈은 그대로 보존.
    """
    if not text:
        return ""
    lines = []
    for line in text.split("\n"):
        # ### 제목 / ## 제목 / # 제목 → <b>제목</b>
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            lines.append(f"<b>{_escape_plain(m.group(1).strip())}</b>")
            continue
        # **굵게** → <b>굵게</b>  (중첩 방지: non-greedy)
        line = re.sub(
            r"\*\*(.+?)\*\*",
            lambda x: f"<b>{_escape_plain(x.group(1))}</b>",
            line,
        )
        # _기울임_ → <i>기울임</i>
        line = re.sub(
            r"(?<!\w)_(.+?)_(?!\w)",
            lambda x: f"<i>{_escape_plain(x.group(1))}</i>",
            line,
        )
        # `코드` → <code>코드</code>
        line = re.sub(
            r"`(.+?)`",
            lambda x: f"<code>{_escape_plain(x.group(1))}</code>",
            line,
        )
        # 나머지 라인의 평문 부분 이스케이프
        # (이미 삽입된 <b>/<i>/<code> 태그를 건드리지 않으려면 태그 분리 필요)
        parts = re.split(r"(<[^>]+>)", line)
        line = "".join(
            p if p.startswith("<") else _escape_plain(p) for p in parts
        )
        lines.append(line)
    return "\n".join(lines)


def _sections_to_telegram_html(sections) -> str:
    """AnalysisView 섹션들을 텔레그램 HTML로 렌더.

    구조화 섹션: <b>제목</b> 다음 줄부터 '• 문장'을 줄바꿈으로 나열.
    레거시 섹션(markdown): 기존 _md_to_telegram_html 경로 사용.
    """
    blocks = []
    for s in sections:
        if s.markdown:
            blocks.append(_md_to_telegram_html(s.markdown))
            continue
        lines = []
        if s.title:
            lines.append(f"<b>{_escape_plain(s.title)}</b>")
        for b in s.bullets:
            lines.append(f"• {_md_to_telegram_html(b)}")
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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
        lines.append(body)
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
    from app.services.analysis_view import build_sections

    sections = build_sections(
        getattr(analysis, "analysis_sections", None),
        getattr(analysis, "full_analysis_md", None),
    )
    body = _sections_to_telegram_html(sections) if sections else (analysis.short_summary_md or "")
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
    # 본문을 비우고 bullets를 줄여가며 한도 내로. 그래도 안 되면 본문·bullets 비워 링크 보존.
    for n in range(len(bullets_list) - 1, -1, -1):
        candidate = render("", bullets_list[:n])
        if len(candidate) <= _TELEGRAM_MAX_LEN:
            return candidate
    stripped = render("", [])
    if len(stripped) <= _TELEGRAM_MAX_LEN:
        return stripped
    return _truncate_html(stripped, _TELEGRAM_MAX_LEN)


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
    *,
    channel_name: str = "",
    tags=None,
    detail: str = "full",
) -> int:
    """그룹의 모든 chat_id에 발송. 성공 건수 반환. 일부 실패해도 나머지는 계속 시도."""
    if not notif.is_sendable:
        return 0
    text = build_message(video, analysis, threshold,
                         channel_name=channel_name, tags=tags or [], detail=detail)
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


async def _fetch_video_tags(make_session, video_pk: int, limit: int = 8) -> list[str]:
    async with make_session() as sess:
        rows = (
            await sess.execute(
                select(Tag.name)
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk == video_pk)
                .order_by(VideoTag.weight.desc().nullslast(), Tag.name.asc())
                .limit(limit)
            )
        ).all()
    return [r[0] for r in rows]


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
    from app.services.monitor_service import (
        _passes_group_baseline,
        _passes_notify_baseline,
    )

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

    apply_group = _should_apply_group_baseline(notif.send_mode, notif.dispatch_scope)
    candidates = [
        (v, a, ch)
        for (v, a, ch) in rows
        if _passes_notify_baseline(ch.notify_from, v.published_at)
        and (
            not apply_group
            or _passes_group_baseline(notif.notify_baseline_at, v.published_at)
        )
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
                for i, (video, analysis, channel) in enumerate(batch):
                    try:
                        tags = await _fetch_video_tags(make_session, video.video_pk)
                        ok = await notify_video(
                            notif, video, analysis, client, threshold,
                            channel_name=getattr(channel, "channel_name", "") or "",
                            tags=tags, detail=notif.message_detail,
                        )
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


def _should_stamp_on_save(*, before_sendable: bool, after_sendable: bool) -> bool:
    """알림 저장 시 발송 기준선을 (재)스탬프할지. false→true 전환에서만 True."""
    return (not before_sendable) and after_sendable


def _needs_baseline_backfill(*, sendable: bool, baseline: object | None) -> bool:
    """기동 업그레이드 보정: 이미 sendable인데 기준선이 비어 있으면 True."""
    return sendable and baseline is None


def _should_apply_group_baseline(send_mode: str, dispatch_scope: str) -> bool:
    """그룹 발송 기준선(notify_baseline_at) 게이트를 적용할지.

    scheduled + all 조합에서만 게이트를 끈다(backlog 포함). 그 외(immediate,
    scheduled+after_activation)는 모두 게이트를 적용해 현행 동작을 유지한다.
    """
    return not (send_mode == "scheduled" and dispatch_scope == "all")


async def backfill_notify_baselines() -> int:
    """기동 보정: sendable인데 기준선이 빈 활성 그룹에 now()를 스탬프한다.

    업그레이드 직후 기존 backlog가 한꺼번에 발송되는 것을 막는다.
    반환: 스탬프한 그룹 수.
    """
    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.services.settings_manager import get_settings_manager

    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True))))
            .scalars()
            .all()
        )

    stamped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for group in groups:
        notif = await mgr.get_notification(group.group_id)
        if _needs_baseline_backfill(
            sendable=notif.is_sendable, baseline=notif.notify_baseline_at
        ):
            await mgr.set_values(
                group.group_id,
                "notification",
                [{"key": "notify_baseline_at", "value": now_iso, "value_type": "string"}],
            )
            stamped += 1
    return stamped
