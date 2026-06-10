"""주간 리뷰(다이제스트) 생성 서비스."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.models.pg.digest import Digest
from app.models.pg.tag import Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.llm_client import LiteLLMClient
from app.services.notify_service import send_telegram
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import DigestSettings
from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# 사용 가능한 placeholder: {category} {period_label} {video_count}
#                          {sentiment_summary} {top_tags} {videos_block}
DEFAULT_DIGEST_PROMPT = """너는 경제·투자 콘텐츠를 종합하는 애널리스트다. 아래는 '{category}' 카테고리에서 {period_label} 동안 분석 완료된 유튜브 영상 {video_count}건의 요약·인사이트 모음이다.

## 집계 정보
- 감성 분포: {sentiment_summary}
- 주요 태그: {top_tags}

## 영상별 자료 (헤드라인 · 한줄요약 · 핵심 주장 · 인사이트 · 등장 종목/지표)
{videos_block}

## 작성 지침
위 영상들을 가로질러 이번 기간의 핵심을 한국어로 '브리핑' 형태로 종합하라. 개별 영상 나열이 아니라, 여러 영상에 걸쳐 반복되는 주장·관점·흐름을 묶어 서술할 것.
- 행위 서술('~을 다뤘다', '~을 분석했다') 금지. 무엇을 주장·전망·결론 내렸는지를 직접 서술.
- 같은 방향의 견해가 여럿이면 '합의된 관점', 견해가 갈리면 '엇갈리는 관점'으로 구분해 대비할 것.
- 인사이트는 시청자가 실제 판단에 쓸 수 있도록 구체적 근거·수치와 함께 정리.
- '~함', '~임' 형태의 개조식. 정치·민감 주제는 사실 위주 중립 표현.

## 출력 형식
반드시 아래 JSON 형식으로만 출력:
{{
  "headline": "이모지 1~2개 포함, 이번 기간 핵심을 한 줄로 (40자 이내)",
  "summary_md": "마크다운 본문. 반드시 다음 4개 섹션(## 제목)을 순서대로 포함: '## 주요 내용'(이번 기간 핵심 주제·이슈), '## 관점과 의견'(합의된 관점 / 엇갈리는 관점 구분), '## 핵심 인사이트'(실행 가능한 판단 근거), '## 주목할 종목·이슈'(등장 종목/지표 중심)",
  "telegram_summary": "텔레그램용 짧은 브리핑 (400자 이내, 마크다운 없이 일반 텍스트). 주요 내용과 핵심 관점 위주."
}}"""


_MAX_VIDEOS_IN_PROMPT = 40
_MAX_BULLETS_PER_VIDEO = 3
_MAX_INSIGHTS_PER_VIDEO = 3
_MAX_ENTITIES_PER_VIDEO = 6

_SENTIMENT_KO = {
    "bullish": "긍정",
    "bearish": "부정",
    "neutral": "중립",
    "mixed": "혼조",
    "unknown": "미상",
}


@dataclass
class VideoBrief:
    channel_name: str
    headline: Optional[str]
    one_line: Optional[str]
    title: Optional[str]
    sentiment: Optional[str]
    bullet_points: Optional[Any] = None
    insights: Optional[Any] = None
    entities: Optional[Any] = None


def split_category_tokens(raw: Optional[str]) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in s.split(","):
        t = part.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _format_entities(entities: Optional[Any]) -> str:
    if not isinstance(entities, list):
        return ""
    names: list[str] = []
    for e in entities:
        name = str((e.get("name") if isinstance(e, dict) else e) or "").strip()
        if name:
            names.append(name)
        if len(names) >= _MAX_ENTITIES_PER_VIDEO:
            break
    return ", ".join(names)


def _sentiment_summary_text(breakdown: dict) -> str:
    parts = []
    for key in ("bullish", "bearish", "neutral", "mixed", "unknown"):
        if breakdown.get(key):
            parts.append(f"{_SENTIMENT_KO[key]} {breakdown[key]}")
    return ", ".join(parts) if parts else "데이터 없음"


def _build_videos_block(videos: list["VideoBrief"], total: int) -> str:
    lines: list[str] = []
    shown = videos[:_MAX_VIDEOS_IN_PROMPT]
    for v in shown:
        head = (v.headline or v.one_line or v.title or "").strip()
        senti = _SENTIMENT_KO.get(v.sentiment or "unknown", v.sentiment or "미상")
        lines.append(f"- [{v.channel_name}] {head} (논조: {senti})")
        if v.one_line and v.one_line.strip() and v.one_line.strip() != head:
            lines.append(f"  {v.one_line.strip()}")
        bullets = v.bullet_points if isinstance(v.bullet_points, list) else []
        for b in bullets[:_MAX_BULLETS_PER_VIDEO]:
            s = str(b).strip()
            if s:
                lines.append(f"  • {s}")
        insights = v.insights if isinstance(v.insights, list) else []
        for ins in insights[:_MAX_INSIGHTS_PER_VIDEO]:
            s = str(ins).strip()
            if s:
                lines.append(f"  ▶ 인사이트: {s}")
        ent = _format_entities(v.entities)
        if ent:
            lines.append(f"  · 등장: {ent}")
    remaining = total - len(shown)
    if remaining > 0:
        lines.append(f"... 외 {remaining}건")
    return "\n".join(lines)


@dataclass
class DigestAggregate:
    video_count: int
    sentiment_breakdown: dict[str, int]
    top_tags: list[dict[str, Any]]
    top_channels: list[dict[str, Any]]
    videos: list["VideoBrief"] = field(default_factory=list)


@dataclass
class DigestGenerated:
    headline: str
    summary_md: str
    telegram_summary: str
    model_name: str


def _period(now_utc: datetime, weeks: int) -> tuple[datetime, datetime]:
    end = now_utc.replace(second=0, microsecond=0)
    start = now_utc - timedelta(days=7 * max(1, weeks))
    return start.replace(second=0, microsecond=0), end


def _period_label(period_start: datetime, period_end: datetime) -> str:
    return f"{period_start.date()} ~ {period_end.date()}"


def _render_payload(agg: DigestAggregate, start: datetime, end: datetime, category: str) -> str:
    payload = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "category": category or None,
        "video_count": agg.video_count,
        "sentiment_breakdown": agg.sentiment_breakdown,
        "top_tags": agg.top_tags,
        "top_channels": agg.top_channels,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def aggregate_period(
    session: AsyncSession,
    period_start: datetime,
    period_end: datetime,
    category: str = "",
) -> DigestAggregate:
    rows = (
        await session.execute(
            select(
                Video.video_pk,
                VideoAnalysis.sentiment,
                VideoAnalysis.headline,
                VideoAnalysis.one_line,
                Video.title,
                VideoAnalysis.bullet_points,
                VideoAnalysis.insights,
                VideoAnalysis.entities,
                Channel.channel_name,
                Channel.category,
            )
            .join(VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk)
            .join(Channel, Channel.channel_pk == Video.channel_pk)
            .where(VideoAnalysis.analyzed_at >= period_start, VideoAnalysis.analyzed_at < period_end)
            .order_by(VideoAnalysis.analyzed_at.desc())
        )
    ).all()

    want = (category or "").strip()
    selected = []
    for r in rows:
        if want:
            if want not in split_category_tokens(r.category):
                continue
        selected.append(r)

    video_pks = [r.video_pk for r in selected]
    sentiment: dict[str, int] = {}
    channel_count: dict[str, int] = {}
    videos: list[VideoBrief] = []
    for r in selected:
        key = (r.sentiment or "unknown").strip() or "unknown"
        sentiment[key] = sentiment.get(key, 0) + 1
        cname = (r.channel_name or "(알 수 없음)").strip() or "(알 수 없음)"
        channel_count[cname] = channel_count.get(cname, 0) + 1
        videos.append(VideoBrief(
            channel_name=cname, headline=r.headline, one_line=r.one_line, title=r.title,
            sentiment=r.sentiment, bullet_points=r.bullet_points,
            insights=r.insights, entities=r.entities,
        ))

    top_channels = [
        {"name": name, "count": count}
        for name, count in sorted(channel_count.items(), key=lambda x: (-x[1], x[0]))[:10]
    ]

    top_tags: list[dict[str, Any]] = []
    if video_pks:
        tag_rows = (
            await session.execute(
                select(Tag.name, func.count(VideoTag.video_pk))
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk.in_(video_pks))
                .group_by(Tag.name)
                .order_by(func.count(VideoTag.video_pk).desc(), Tag.name.asc())
                .limit(20)
            )
        ).all()
        top_tags = [{"name": n, "count": int(c)} for n, c in tag_rows]

    return DigestAggregate(
        video_count=len(video_pks),
        sentiment_breakdown=sentiment,
        top_tags=top_tags,
        top_channels=top_channels,
        videos=videos,
    )


async def synthesize_with_llm(group_id: int, aggregate: DigestAggregate, period_start: datetime, period_end: datetime, category: str = "") -> DigestGenerated:
    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group_id)
    prompts = await mgr.get_prompts(group_id)
    model = ai.digest_model or ai.primary_model
    prompt = (prompts.digest_prompt or DEFAULT_DIGEST_PROMPT).strip()
    period_label = _period_label(period_start, period_end)
    try:
        user_msg = prompt.format(
            category=category or "전체",
            period_label=period_label,
            video_count=aggregate.video_count,
            sentiment_summary=_sentiment_summary_text(aggregate.sentiment_breakdown),
            top_tags=", ".join(t["name"] for t in aggregate.top_tags[:8]) or "없음",
            videos_block=_build_videos_block(aggregate.videos, aggregate.video_count),
        )
    except (KeyError, IndexError, ValueError):
        # 프롬프트에 알 수 없는 placeholder가 있으면 안전 폴백(발송 자체는 막지 않음).
        context_json = _render_payload(aggregate, period_start, period_end, category)
        videos_block = _build_videos_block(aggregate.videos, aggregate.video_count)
        user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}\n\n영상별 자료:\n{videos_block}"
    client = LiteLLMClient(ai)
    try:
        chat = await client.chat(
            model=model,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.2,
            max_tokens=min(ai.max_tokens or 4096, 4096),
            response_format={"type": "json_object"},
        )
        data = json.loads(chat.content)
        headline = str(data.get("headline") or "").strip()
        summary_md = str(data.get("summary_md") or "").strip()
        telegram_summary = str(data.get("telegram_summary") or "").strip()
        if not headline:
            headline = "주간 리뷰 브리핑"
        if not summary_md:
            summary_md = f"- 분석 영상 수: {aggregate.video_count}\n- 감성 분포: {aggregate.sentiment_breakdown}"
        if not telegram_summary:
            telegram_summary = summary_md[:900]
        return DigestGenerated(
            headline=headline,
            summary_md=summary_md,
            telegram_summary=telegram_summary[:900],
            model_name=model,
        )
    finally:
        await client.aclose()


def _fallback_generated(aggregate: DigestAggregate, period_start: datetime, period_end: datetime) -> DigestGenerated:
    headline = "주간 리뷰 브리핑 (Fallback)"
    lines = [
        f"- 기간: {period_start.date()} ~ {period_end.date()}",
        f"- 분석 영상 수: {aggregate.video_count}",
        f"- 감성 분포: {aggregate.sentiment_breakdown or {}}",
        "- 상위 태그:",
    ]
    lines.extend([f"  - {t['name']}: {t['count']}" for t in aggregate.top_tags[:10]])
    lines.append("- 상위 채널:")
    lines.extend([f"  - {c['name']}: {c['count']}" for c in aggregate.top_channels[:10]])
    summary = "\n".join(lines)
    return DigestGenerated(
        headline=headline,
        summary_md=summary,
        telegram_summary=summary[:900],
        model_name="fallback",
    )


def _build_digest_share_url(slug: str, share_token: Optional[str]) -> str:
    from app.config import settings as app_settings

    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    if not base or not slug or not share_token:
        return ""
    return f"{base}/d/{slug}/{share_token}"


def build_digest_telegram_text(
    *,
    headline: str,
    telegram_summary: str,
    slug: str,
    share_token: Optional[str],
    share_link_enabled: bool,
) -> str:
    text = f"<b>{headline}</b>\n\n{telegram_summary}"
    if share_link_enabled:
        url = _build_digest_share_url(slug, share_token)
        if url:
            text += f'\n\n📖 <a href="{escape(url, quote=True)}">웹에서 자세히 보기</a>'
    return text


async def _send_digest_telegram(
    group_id: int,
    headline: str,
    telegram_summary: str,
    *,
    slug: str,
    share_token: Optional[str],
    share_link_enabled: bool,
) -> None:
    notif = await get_settings_manager().get_notification(group_id)
    if not notif.is_sendable:
        return
    text = build_digest_telegram_text(
        headline=headline,
        telegram_summary=telegram_summary,
        slug=slug,
        share_token=share_token,
        share_link_enabled=share_link_enabled,
    )
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        for chat_id in notif.chat_ids:
            await send_telegram(client, notif.bot_token, chat_id, text, notif.parse_mode)


async def generate_digest_for_group(
    group: Group,
    digest_cfg: DigestSettings,
    period_weeks: Optional[int] = None,
    category: Optional[str] = None,
    save: bool = True,
) -> Digest:
    await dpm.ensure_schema(group)
    engine = await dpm.get_engine_for_group(group)
    make_session = lambda: dpm.session_for_group(engine, group.schema_name)

    weeks = max(1, int(period_weeks or digest_cfg.period_weeks or 1))
    cat = (category if category is not None else digest_cfg.category).strip()
    now = datetime.now(timezone.utc)
    period_start, period_end = _period(now, weeks)

    async with make_session() as session:
        if save:
            existing = (
                await session.execute(
                    select(Digest).where(
                        Digest.period_type == "weekly",
                        Digest.period_weeks == weeks,
                        Digest.period_start == period_start,
                        Digest.period_end == period_end,
                        Digest.category == (cat or None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        agg = await aggregate_period(session, period_start, period_end, cat)
        status = "done"
        error = None
        try:
            generated = await synthesize_with_llm(group.group_id, agg, period_start, period_end, cat)
        except Exception as e:
            generated = _fallback_generated(agg, period_start, period_end)
            status = "fallback"
            error = str(e)[:500]

        digest = Digest(
            period_type="weekly",
            period_weeks=weeks,
            period_start=period_start,
            period_end=period_end,
            category=cat or None,
            video_count=agg.video_count,
            headline=generated.headline,
            summary_md=generated.summary_md,
            telegram_summary=generated.telegram_summary,
            sentiment_breakdown=agg.sentiment_breakdown,
            top_tags=agg.top_tags,
            top_channels=agg.top_channels,
            model_name=generated.model_name,
            share_token=generate_share_token(),
            share_visibility=DEFAULT_VISIBILITY,
            status=status,
            error=error,
        )
        if save:
            session.add(digest)
            await session.commit()
            await session.refresh(digest)
        return digest


def _is_due_now(now_local: datetime, cfg: DigestSettings) -> bool:
    idx = _DAY_INDEX.get(cfg.schedule_day, 6)
    if now_local.weekday() != idx:
        return False
    try:
        hh, mm = cfg.schedule_time.split(":")
        return now_local.hour == int(hh) and now_local.minute == int(mm)
    except Exception:
        return False


async def run_digest_tick_once() -> None:
    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all()
        )
    for group in groups:
        try:
            cfg = await mgr.get_digest(group.group_id)
            if not cfg.enabled:
                continue
            from zoneinfo import ZoneInfo

            now_local = datetime.now(ZoneInfo(cfg.timezone))
            if not _is_due_now(now_local, cfg):
                continue
            digest = await generate_digest_for_group(group, cfg, save=True)
            if cfg.telegram_enabled and digest.telegram_summary:
                await _send_digest_telegram(
                    group.group_id,
                    digest.headline or "주간 리뷰",
                    digest.telegram_summary,
                    slug=group.slug,
                    share_token=digest.share_token,
                    share_link_enabled=cfg.share_link_enabled,
                )
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] digest tick 실패: {e}")
