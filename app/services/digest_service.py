"""다이제스트 생성 서비스."""

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
from app.services.ai_usage_service import BudgetExceeded, budget_ok_for_group, record_usage
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.digest_sections import (
    assemble_output_sections,
    build_structured_prompt,
    parse_structured_response,
    resolve_sections,
    sections_to_markdown,
    SECTION_KIND_LLM,
)
from app.services.global_settings import resolve_ai_gateway
from app.services.job_logger import (
    JOB_TYPE_DIGEST,
    STATUS_FAIL,
    STATUS_SKIP,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.llm_client import LiteLLMClient
from app.services.notify_service import resolve_notify_target, send_telegram
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import DigestScheduleConfig, period_label_from_days, period_type_from_days
from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# 사용 가능한 placeholder: {category} {period_label} {video_count}
#                          {sentiment_summary} {top_tags} {top_channels}
#                          {top_viewed} {previous_digest} {videos_block}
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
    view_count: Optional[int] = None
    published_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None


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


def _fmt_views(n: Optional[int]) -> str:
    if not n or n <= 0:
        return ""
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    if n >= 1000:
        return f"{n / 1000:.1f}천"
    return str(n)


def _video_meta_suffix(v: "VideoBrief") -> str:
    parts: list[str] = []
    views = _fmt_views(v.view_count)
    if views:
        parts.append(f"조회 {views}")
    if v.published_at:
        parts.append(f"{v.published_at.month}/{v.published_at.day}")
    return f" · {' · '.join(parts)}" if parts else ""


def _build_top_viewed_block(videos: list["VideoBrief"], limit: int = 6) -> str:
    ranked = sorted(
        (v for v in videos if v.view_count and v.view_count > 0),
        key=lambda v: v.view_count or 0,
        reverse=True,
    )[:limit]
    if not ranked:
        return "데이터 없음"
    lines: list[str] = []
    for v in ranked:
        head = (v.headline or v.one_line or v.title or "").strip()
        lines.append(f"- [{v.channel_name}] {head}{_video_meta_suffix(v)}")
    return "\n".join(lines)


def _build_videos_block(videos: list["VideoBrief"], total: int) -> str:
    lines: list[str] = []
    shown = videos[:_MAX_VIDEOS_IN_PROMPT]
    for v in shown:
        head = (v.headline or v.one_line or v.title or "").strip()
        senti = _SENTIMENT_KO.get(v.sentiment or "unknown", v.sentiment or "미상")
        lines.append(f"- [{v.channel_name}] {head} (논조: {senti}){_video_meta_suffix(v)}")
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
    sections: list[dict] = field(default_factory=list)


def _period(as_of: datetime, period_days: int) -> tuple[datetime, datetime]:
    """집계 기간 [start, end). end는 기준 시각(as_of)을 분 단위로 절삭한 값."""
    days = period_days if period_days in (1, 7, 30) else 7
    end = as_of.replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    return start, end


def _fallback_headline(period_days: int) -> str:
    return f"{period_label_from_days(period_days)} 리뷰 브리핑"


def _period_label(period_start: datetime, period_end: datetime) -> str:
    return f"{period_start.date()} ~ {period_end.date()}"


def _format_previous_digest(prev: Optional["Digest"]) -> str:
    """직전 리포트를 프롬프트용 압축 컨텍스트로. 추세 비교에 사용."""
    if prev is None:
        return "없음 (직전 리포트 없음 — 추세 비교 생략)"
    parts: list[str] = []
    if prev.headline:
        parts.append(f"직전 헤드라인: {prev.headline.strip()}")
    if prev.telegram_summary:
        parts.append(prev.telegram_summary.strip())
    text = "\n".join(parts).strip()
    return text or "없음 (직전 리포트 내용 없음)"


async def _fetch_previous_digest(
    session: AsyncSession,
    *,
    period_days: int,
    category: Optional[str],
    before: datetime,
    digest_config_id: Optional[str] = None,
) -> Optional["Digest"]:
    """현재 기간 직전의 동일 설정·주기 리포트 1건."""
    if digest_config_id:
        return (
            await session.execute(
                select(Digest)
                .where(
                    Digest.digest_config_id == digest_config_id,
                    Digest.period_days == period_days,
                    Digest.category == (category or None),
                    Digest.period_end <= before,
                    Digest.status.in_(["done", "fallback"]),
                )
                .order_by(Digest.period_end.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    weeks = max(1, period_days // 7)
    return (
        await session.execute(
            select(Digest)
            .where(
                Digest.digest_config_id.is_(None),
                Digest.period_type == "weekly",
                Digest.period_weeks == weeks,
                Digest.category == (category or None),
                Digest.period_end <= before,
                Digest.status.in_(["done", "fallback"]),
            )
            .order_by(Digest.period_end.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


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
                Video.view_count,
                Video.published_at,
                Video.duration_seconds,
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
            view_count=r.view_count, published_at=r.published_at,
            duration_seconds=r.duration_seconds,
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


async def synthesize_with_llm(
    group_id: int,
    aggregate: DigestAggregate,
    period_start: datetime,
    period_end: datetime,
    category: str = "",
    previous_digest: str = "없음",
    digest_prompt: str = "",
    period_days: int = 7,
    owner_user_id: Optional[int] = None,
) -> DigestGenerated:
    ai = await resolve_ai_gateway(group_id)
    from app.services.preset_service import resolve_prompts

    prompts = await resolve_prompts(group_id)
    model = ai.digest_model or ai.primary_model
    period_label = _period_label(period_start, period_end)

    custom_prompt = (digest_prompt or prompts.digest_prompt or "").strip()
    if custom_prompt:
        prompt = custom_prompt
        try:
            user_msg = prompt.format(
                category=category or "전체",
                period_label=period_label,
                video_count=aggregate.video_count,
                sentiment_summary=_sentiment_summary_text(aggregate.sentiment_breakdown),
                top_tags=", ".join(t["name"] for t in aggregate.top_tags[:8]) or "없음",
                top_channels=", ".join(f"{c['name']}({c['count']})" for c in aggregate.top_channels[:10]) or "없음",
                top_viewed=_build_top_viewed_block(aggregate.videos),
                previous_digest=previous_digest,
                videos_block=_build_videos_block(aggregate.videos, aggregate.video_count),
            )
        except (KeyError, IndexError, ValueError):
            # 프롬프트에 알 수 없는 placeholder가 있으면 안전 폴백(발송 자체는 막지 않음).
            context_json = _render_payload(aggregate, period_start, period_end, category)
            videos_block = _build_videos_block(aggregate.videos, aggregate.video_count)
            user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}\n\n영상별 자료:\n{videos_block}"
        sections_spec = None
    else:
        profile = await get_settings_manager().get_profile(group_id)
        sections_spec = resolve_sections([], profile.digest_sections)
        data_block = (
            f"기간: {period_label}\n"
            f"분석 영상 수: {aggregate.video_count}\n"
            f"감성 분포: {_sentiment_summary_text(aggregate.sentiment_breakdown)}\n"
            f"주요 태그: {', '.join(t['name'] for t in aggregate.top_tags[:8]) or '없음'}\n"
            f"직전 리포트: {previous_digest}\n\n"
            f"영상별 자료:\n{_build_videos_block(aggregate.videos, aggregate.video_count)}"
        )
        user_msg = build_structured_prompt(
            persona=getattr(profile, "persona", ""),
            data_block=data_block, sections=sections_spec,
        )

    client = LiteLLMClient(ai)
    try:
        chat = await client.chat(
            model=model,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.2,
            max_tokens=min(ai.max_tokens or 4096, 4096),
            response_format={"type": "json_object"},
        )
        # 다이제스트는 그룹 개인화 호출 — 그룹 owner 몫으로 원장 기록 (스펙 §2.4)
        await record_usage(
            user_id=owner_user_id,
            group_id=group_id,
            purpose="digest",
            model=model,
            input_tokens=chat.input_tokens,
            output_tokens=chat.output_tokens,
        )
        if sections_spec is None:
            data = json.loads(chat.content)
            headline = str(data.get("headline") or "").strip() or _fallback_headline(period_days)
            summary_md = str(data.get("summary_md") or "").strip() or \
                f"- 분석 영상 수: {aggregate.video_count}\n- 감성 분포: {aggregate.sentiment_breakdown}"
            telegram_summary = str(data.get("telegram_summary") or "").strip() or summary_md[:900]
            return DigestGenerated(
                headline=headline, summary_md=summary_md,
                telegram_summary=telegram_summary[:900], model_name=model,
            )
        llm_keys = [s["key"] for s in sections_spec if s["kind"] == SECTION_KIND_LLM]
        headline, bodies, telegram_summary = parse_structured_response(
            chat.content, requested_keys=llm_keys
        )
        out_sections = assemble_output_sections(sections_spec, llm_bodies=bodies, agg=aggregate)
        summary_md = sections_to_markdown(out_sections)
        if not headline:
            headline = _fallback_headline(period_days)
        if not summary_md:
            summary_md = f"- 분석 영상 수: {aggregate.video_count}"
        if not telegram_summary:
            telegram_summary = summary_md[:900]
        return DigestGenerated(
            headline=headline, summary_md=summary_md,
            telegram_summary=telegram_summary[:900], model_name=model,
            sections=out_sections,
        )
    finally:
        await client.aclose()


def _fallback_generated(
    aggregate: DigestAggregate, period_start: datetime, period_end: datetime, period_days: int = 7
) -> DigestGenerated:
    headline = f"{_fallback_headline(period_days)} (Fallback)"
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
    owner_user_id: Optional[int] = None,
) -> None:
    notif = await get_settings_manager().get_notification(group_id)
    notif = await resolve_notify_target(owner_user_id, notif)
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
    digest_cfg: DigestScheduleConfig,
    *,
    period_days: Optional[int] = None,
    category: Optional[str] = None,
    save: bool = True,
    as_of: Optional[datetime] = None,
) -> Digest:
    # 월 예산 게이트 (설계 §7): 다이제스트는 owner 귀속 비용 — 초과 시 생성 자체를 막는다.
    # 수동 API는 라우터가 400으로, 스케줄 틱은 아래 run_digest_tick_once가 skip으로 변환.
    ok, reason = await budget_ok_for_group(group)
    if not ok:
        raise BudgetExceeded(reason, limit=0, current=0)

    await dpm.ensure_schema(group)
    engine = await dpm.get_engine_for_group(group)
    make_session = lambda: dpm.session_for_group(engine, group.schema_name)

    days = period_days if period_days in (1, 7, 30) else digest_cfg.period_days
    if days not in (1, 7, 30):
        days = 7
    cat = (category if category is not None else digest_cfg.category).strip()
    anchor = as_of or datetime.now(timezone.utc)
    period_start, period_end = _period(anchor, days)
    config_id = digest_cfg.id

    async with make_session() as session:
        if save:
            existing = (
                await session.execute(
                    select(Digest).where(
                        Digest.digest_config_id == config_id,
                        Digest.period_days == days,
                        Digest.period_start == period_start,
                        Digest.period_end == period_end,
                        Digest.category == (cat or None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        agg = await aggregate_period(session, period_start, period_end, cat)
        prev = await _fetch_previous_digest(
            session,
            period_days=days,
            category=cat or None,
            before=period_start,
            digest_config_id=config_id,
        )
        previous_digest = _format_previous_digest(prev)
        status = "done"
        error = None
        try:
            generated = await synthesize_with_llm(
                group.group_id,
                agg,
                period_start,
                period_end,
                cat,
                previous_digest=previous_digest,
                digest_prompt=digest_cfg.digest_prompt,
                period_days=days,
                owner_user_id=group.owner_user_id,
            )
        except Exception as e:
            generated = _fallback_generated(agg, period_start, period_end, days)
            status = "fallback"
            error = str(e)[:500]

        digest = Digest(
            period_type=period_type_from_days(days),
            period_weeks=max(1, days // 7),
            period_days=days,
            digest_config_id=config_id,
            config_name=digest_cfg.name or None,
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
            digest_sections=generated.sections or None,
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


def _parse_schedule_time(schedule_time: str) -> tuple[int, int] | None:
    try:
        hh_str, mm_str = schedule_time.split(":")
        hh, mm = int(hh_str), int(mm_str)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _most_recent_weekly(
    now_local: datetime, schedule_day: str, schedule_time: str
) -> Optional[datetime]:
    """now_local(로컬 tz aware) 기준 (요일, HH:MM) 스케줄의 가장 최근 발생 시각."""
    idx = _DAY_INDEX.get(schedule_day, 6)
    parsed = _parse_schedule_time(schedule_time)
    if parsed is None:
        return None
    hh, mm = parsed
    days_since = (now_local.weekday() - idx) % 7
    occ = (now_local - timedelta(days=days_since)).replace(
        hour=hh, minute=mm, second=0, microsecond=0
    )
    if occ > now_local:
        occ -= timedelta(days=7)
    return occ


def _most_recent_daily(now_local: datetime, schedule_time: str) -> Optional[datetime]:
    parsed = _parse_schedule_time(schedule_time)
    if parsed is None:
        return None
    hh, mm = parsed
    occ = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if occ > now_local:
        occ -= timedelta(days=1)
    return occ


def _clamp_dom(year: int, month: int, dom: int) -> int:
    """dom이 해당 월 일수를 넘으면 월말로 clamp."""
    import calendar

    last = calendar.monthrange(year, month)[1]
    return min(max(1, dom), last)


def _most_recent_monthly(now_local: datetime, schedule_dom: int, schedule_time: str) -> Optional[datetime]:
    parsed = _parse_schedule_time(schedule_time)
    if parsed is None:
        return None
    hh, mm = parsed
    dom = max(1, min(28, int(schedule_dom)))
    day = _clamp_dom(now_local.year, now_local.month, dom)
    occ = now_local.replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
    if occ > now_local:
        if now_local.month == 1:
            prev_year, prev_month = now_local.year - 1, 12
        else:
            prev_year, prev_month = now_local.year, now_local.month - 1
        prev_day = _clamp_dom(prev_year, prev_month, dom)
        occ = occ.replace(year=prev_year, month=prev_month, day=prev_day)
    return occ


def compute_occurrence(cfg: DigestScheduleConfig, now_local: datetime) -> Optional[datetime]:
    if cfg.period_days == 1:
        return _most_recent_daily(now_local, cfg.schedule_time)
    if cfg.period_days == 30:
        return _most_recent_monthly(now_local, cfg.schedule_dom, cfg.schedule_time)
    return _most_recent_weekly(now_local, cfg.schedule_day, cfg.schedule_time)


def catch_up_window(cfg: DigestScheduleConfig) -> timedelta:
    if cfg.period_days == 1:
        return timedelta(days=1)
    if cfg.period_days == 30:
        return timedelta(days=31)
    return timedelta(days=7)


def _most_recent_occurrence(
    now_local: datetime, schedule_day: str, schedule_time: str
) -> Optional[datetime]:
    """레거시 호환 alias."""
    return _most_recent_weekly(now_local, schedule_day, schedule_time)


async def _digest_exists_for_period(
    session: AsyncSession,
    *,
    digest_config_id: str,
    period_days: int,
    period_start: datetime,
    period_end: datetime,
    category: Optional[str],
) -> bool:
    found = (
        await session.execute(
            select(Digest.digest_pk).where(
                Digest.digest_config_id == digest_config_id,
                Digest.period_days == period_days,
                Digest.period_start == period_start,
                Digest.period_end == period_end,
                Digest.category == (category or None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def run_digest_tick_once() -> None:
    """매 1분 호출. 활성 그룹·설정별로 최근 스케줄 발생분이 없으면 생성·발송."""
    from zoneinfo import ZoneInfo

    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all()
        )
    for group in groups:
        try:
            configs = await mgr.get_digest_configs(group.group_id)
            share = await mgr.get_digest_share_settings(group.group_id)
            if not configs:
                continue
            for cfg in configs:
                if not cfg.enabled:
                    continue
                try:
                    tz = ZoneInfo(cfg.timezone)
                except Exception:
                    tz = ZoneInfo("Asia/Seoul")
                now_local = datetime.now(tz)
                occ_local = compute_occurrence(cfg, now_local)
                if occ_local is None:
                    continue
                if now_local - occ_local > catch_up_window(cfg):
                    continue

                cat = (cfg.category or "").strip()
                occ_utc = occ_local.astimezone(timezone.utc)
                period_start, period_end = _period(occ_utc, cfg.period_days)

                await dpm.ensure_schema(group)
                engine = await dpm.get_engine_for_group(group)
                make_session = lambda: dpm.session_for_group(engine, group.schema_name)

                async with make_session() as session:
                    if await _digest_exists_for_period(
                        session,
                        digest_config_id=cfg.id,
                        period_days=cfg.period_days,
                        period_start=period_start,
                        period_end=period_end,
                        category=cat or None,
                    ):
                        continue

                timer = JobTimer()
                try:
                    with timer:
                        digest = await generate_digest_for_group(
                            group, cfg, save=True, as_of=occ_utc
                        )
                        if cfg.telegram_enabled and digest.telegram_summary:
                            await _send_digest_telegram(
                                group.group_id,
                                digest.headline or _fallback_headline(cfg.period_days),
                                digest.telegram_summary,
                                slug=group.slug,
                                share_token=digest.share_token,
                                share_link_enabled=share.share_link_enabled,
                                owner_user_id=group.owner_user_id,
                            )
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_DIGEST,
                        status=STATUS_SUCCESS if digest.status != "fallback" else STATUS_SKIP,
                        message=(
                            f"{cfg.name or 'Digest'} 생성 — {digest.headline} (영상 {digest.video_count}건)"
                            + (f" / LLM 실패 폴백: {digest.error}" if digest.status == "fallback" else "")
                        ),
                        duration_ms=timer.elapsed_ms,
                    )
                except BudgetExceeded as e:
                    # 예산은 owner당 group 전역 — 남은 config도 모두 초과이므로 그룹 skip.
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_DIGEST,
                        status=STATUS_SKIP,
                        message=f"{cfg.name or 'Digest'} 월 예산 초과로 skip: {e.detail}",
                        duration_ms=timer.elapsed_ms,
                    )
                    print(f"[digest] {group.slug} 월 예산 초과로 skip: {e.detail}")
                    break
                except Exception as e:
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_DIGEST,
                        status=STATUS_FAIL,
                        message=f"{cfg.name or 'Digest'} 생성 실패: {e}",
                        duration_ms=timer.elapsed_ms,
                    )
                    print(f"[{group.slug}] digest tick 실패 ({cfg.name}): {e}")
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] digest tick 실패: {e}")
