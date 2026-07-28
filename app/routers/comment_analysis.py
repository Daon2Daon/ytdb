"""그룹 댓글 반응 분석 API.

videos.py가 이미 590줄이라 여기에 더 얹지 않고 별도 라우터로 분리한다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, update

from app.models.control.group import Group
from app.models.pg.comment_analysis import (
    STATUS_RUNNING,
    CommentAnalysis,
)
from app.models.pg.video import Video
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.schemas.comment_analysis import (
    CommentAnalysisListItem,
    CommentAnalysisOut,
    StartByUrlRequest,
    StartCommentAnalysisRequest,
    StartCommentAnalysisResponse,
)
from app.control_db import get_sessionmaker
from app.services.ai_usage_service import BudgetExceeded, check_monthly_budget
from app.services.comment_analysis_service import run_comment_analysis
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.quota_service import QuotaExceeded, check_comment_analysis_quota
from app.services.yt_quota_service import system_hard_blocked

router = APIRouter(prefix="/api/groups/{slug}", tags=["comment-analysis"])


async def _preflight(user: CurrentUser, requested_limit: int) -> None:
    """쿼터 → 예산 → YouTube 하드 게이트 순으로 검사. 모두 400으로 변환."""
    async with get_sessionmaker()() as session:
        try:
            await check_comment_analysis_quota(session, user.user_id, requested_limit)
            await check_monthly_budget(session, user.user_id)
        except QuotaExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail) from e
        except BudgetExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail) from e
    if await system_hard_blocked():
        raise HTTPException(
            status_code=400, detail="YouTube API 할당량을 초과했습니다."
        )


async def _start(
    group: Group, video_pk: int, video_id: str, user: CurrentUser,
    requested_limit: int, background: BackgroundTasks,
) -> StartCommentAnalysisResponse:
    """행을 running으로 만들고 백그라운드 작업을 등록한다."""
    async with dpm.group_session(group) as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(CommentAnalysis).where(CommentAnalysis.video_pk == video_pk)
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == STATUS_RUNNING:
                raise HTTPException(status_code=409, detail="이미 분석이 진행 중입니다.")
            if existing is None:
                session.add(
                    CommentAnalysis(
                        video_pk=video_pk,
                        status=STATUS_RUNNING,
                        requested_limit=requested_limit,
                    )
                )
            else:
                await session.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(
                        status=STATUS_RUNNING,
                        requested_limit=requested_limit,
                        error=None,
                    )
                )
    background.add_task(
        run_comment_analysis, group, video_pk, video_id, user.user_id, requested_limit
    )
    return StartCommentAnalysisResponse(
        video_pk=video_pk, status=STATUS_RUNNING, queued=True
    )


@router.post(
    "/videos/{video_pk}/comment-analysis",
    response_model=StartCommentAnalysisResponse,
    status_code=202,
)
async def start_comment_analysis(
    video_pk: int,
    payload: StartCommentAnalysisRequest,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> StartCommentAnalysisResponse:
    await _preflight(user, payload.limit)
    async with dpm.group_session(group) as session:
        video_id = (
            await session.execute(
                select(Video.video_id).where(Video.video_pk == video_pk)
            )
        ).scalar_one_or_none()
    if video_id is None:
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    return await _start(group, video_pk, video_id, user, payload.limit, background)


@router.get(
    "/videos/{video_pk}/comment-analysis", response_model=CommentAnalysisOut
)
async def get_comment_analysis(
    video_pk: int,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> CommentAnalysisOut:
    async with dpm.group_session(group) as session:
        row = (
            await session.execute(
                select(CommentAnalysis).where(CommentAnalysis.video_pk == video_pk)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
        current = (
            await session.execute(
                select(Video.comment_count).where(Video.video_pk == video_pk)
            )
        ).scalar_one_or_none()
    return CommentAnalysisOut(
        video_pk=row.video_pk,
        status=row.status,
        requested_limit=row.requested_limit,
        fetched_count=row.fetched_count,
        total_count=row.total_count,
        current_comment_count=current,
        positive_count=row.positive_count,
        negative_count=row.negative_count,
        neutral_count=row.neutral_count,
        result=row.result,
        model=row.model,
        partial=row.partial,
        error=row.error,
        analyzed_at=row.analyzed_at,
    )


@router.get("/comment-analyses", response_model=list[CommentAnalysisListItem])
async def list_comment_analyses(
    limit: int = 20,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> list[CommentAnalysisListItem]:
    """최근 갱신순 목록 — URL로 분석한 영상을 다시 찾아가는 경로."""
    async with dpm.group_session(group) as session:
        rows = (
            await session.execute(
                select(CommentAnalysis, Video)
                .join(Video, Video.video_pk == CommentAnalysis.video_pk)
                .order_by(CommentAnalysis.updated_at.desc())
                .limit(min(max(1, limit), 100))
            )
        ).all()
    return [
        CommentAnalysisListItem(
            video_pk=ca.video_pk,
            video_id=v.video_id,
            title=v.title,
            thumbnail_url=v.thumbnail_url,
            status=ca.status,
            positive_count=ca.positive_count,
            negative_count=ca.negative_count,
            neutral_count=ca.neutral_count,
            analyzed_at=ca.analyzed_at,
        )
        for ca, v in rows
    ]


@router.post(
    "/comment-analysis/by-url",
    response_model=StartCommentAnalysisResponse,
    status_code=202,
)
async def start_by_url(
    payload: StartByUrlRequest,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> StartCommentAnalysisResponse:
    """미등록 영상은 videos에 먼저 등록한 뒤 같은 경로로 합류시킨다."""
    from app.routers.videos import ensure_video_row

    await _preflight(user, payload.limit)
    video_pk, video_id = await ensure_video_row(group, payload.video_url)
    return await _start(group, video_pk, video_id, user, payload.limit, background)
