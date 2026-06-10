"""공개 공유 페이지(무인증). GET /v/{slug}/{token} → 매거진 HTML."""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import settings as app_settings
from app.models.pg.digest import Digest
from app.models.pg.tag import Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.routers.deps import get_group_by_slug_or_404
from app.services.analysis_view import build_sections
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.share_page import render_digest_share_html, render_share_html

router = APIRouter(tags=["share"])


def _kst(dt) -> str:
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return str(dt)


@router.get("/v/{slug}/{token}", response_class=HTMLResponse, include_in_schema=False)
async def share_page(slug: str, token: str) -> HTMLResponse:
    group = await get_group_by_slug_or_404(slug)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.share_token == token))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404)
        visibility = video.share_visibility or "unlisted"
        if visibility != "unlisted":
            raise HTTPException(status_code=404)
        analysis = (
            await session.execute(
                select(VideoAnalysis).where(VideoAnalysis.video_pk == video.video_pk)
            )
        ).scalar_one_or_none()
        tag_rows = (
            await session.execute(
                select(Tag.name)
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk == video.video_pk)
            )
        ).scalars().all()

    sections = build_sections(
        getattr(analysis, "analysis_sections", None) if analysis else None,
        getattr(analysis, "full_analysis_md", None) if analysis else None,
    )
    canonical = f"{app_settings.PUBLIC_BASE_URL}/v/{slug}/{token}"
    html = render_share_html(
        title=video.title,
        headline=getattr(analysis, "headline", None) if analysis else None,
        one_line=getattr(analysis, "one_line", None) if analysis else None,
        thumbnail_url=video.thumbnail_url,
        canonical_url=canonical,
        sections=sections,
        tags=list(tag_rows),
        published_at_kst=_kst(video.published_at),
    )
    return HTMLResponse(content=html)


@router.get("/d/{slug}/{token}", response_class=HTMLResponse, include_in_schema=False)
async def digest_share_page(slug: str, token: str) -> HTMLResponse:
    group = await get_group_by_slug_or_404(slug)
    async with dpm.group_session(group) as session:
        digest = (
            await session.execute(select(Digest).where(Digest.share_token == token))
        ).scalar_one_or_none()
        if digest is None:
            raise HTTPException(status_code=404)
        if (digest.share_visibility or "unlisted") != "unlisted":
            raise HTTPException(status_code=404)
        period_label = f"{_kst(digest.period_start)} ~ {_kst(digest.period_end)}"
        html = render_digest_share_html(
            headline=digest.headline,
            summary_md=digest.summary_md,
            period_label=period_label,
            video_count=digest.video_count or 0,
            category=digest.category,
            canonical_url=f"{app_settings.PUBLIC_BASE_URL}/d/{slug}/{token}",
        )
    return HTMLResponse(content=html)
