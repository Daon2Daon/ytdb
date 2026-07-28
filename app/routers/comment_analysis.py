"""그룹 댓글 반응 분석 API.

videos.py가 이미 590줄이라 여기에 더 얹지 않고 별도 라우터로 분리한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

# running 행이 이보다 오래 갱신되지 않았으면 죽은 작업으로 보고 재시작을 허용한다.
# 프로세스가 분석 도중 죽으면 _fail이 실행되지 않아 행이 running으로 남는데,
# 이 유예가 없으면 해당 영상은 영구히 409가 되어 UI로 복구할 수 없다.
# 상한 5,000건이 2~4분, 프론트 폴링 타임아웃이 10분이므로 그보다 넉넉히 잡는다.
STALE_RUNNING_MINUTES = 15


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
    """행을 running으로 만들고 백그라운드 작업을 등록한다.

    삽입과 진행중 검사를 UPSERT 한 문장으로 처리한다. SELECT 후 INSERT로 나누면
    동시 요청 두 건이 모두 SELECT를 미스해 UNIQUE(video_pk) 위반(500)이 난다.
    ON CONFLICT DO UPDATE의 WHERE가 걸러지면 반환 행이 없고, 그것이 곧 409다.
    """
    table = CommentAnalysis.__table__
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=STALE_RUNNING_MINUTES)
    async with dpm.group_session(group) as session:
        async with session.begin():
            stmt = (
                pg_insert(table)
                .values(
                    video_pk=video_pk,
                    status=STATUS_RUNNING,
                    requested_limit=requested_limit,
                )
                .on_conflict_do_update(
                    index_elements=["video_pk"],
                    set_={
                        "status": STATUS_RUNNING,
                        "requested_limit": requested_limit,
                        "error": None,
                    },
                    # 진행 중인 작업은 덮지 않는다. 단 오래 멈춰 있으면 죽은 것으로 보고 인수한다.
                    where=or_(
                        table.c.status != STATUS_RUNNING,
                        table.c.updated_at < stale_before,
                    ),
                )
                .returning(table.c.video_pk)
            )
            claimed = (await session.execute(stmt)).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="이미 분석이 진행 중입니다.")
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
