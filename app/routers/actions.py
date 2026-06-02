"""그룹 수동 트리거 API (즉시 폴링 / 즉시 분석).

장시간 작업이므로 백그라운드로 실행하고 즉시 202를 반환한다. 결과는
영상 목록/로그 조회로 확인한다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.services.monitor_service import analyze_group, poll_group

router = APIRouter(prefix="/api/groups/{slug}/actions", tags=["actions"])


@router.post("/poll", status_code=202)
async def trigger_poll(
    background: BackgroundTasks, group: Group = Depends(get_group_or_404)
) -> dict:
    background.add_task(poll_group, group)
    return {"status": "started", "action": "poll", "group": group.slug}


@router.post("/analyze", status_code=202)
async def trigger_analyze(
    background: BackgroundTasks, group: Group = Depends(get_group_or_404)
) -> dict:
    background.add_task(analyze_group, group)
    return {"status": "started", "action": "analyze", "group": group.slug}
