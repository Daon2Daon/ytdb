"""관리자 수동 트리거: 기존 구조화 분석 행에 records_extractor를 소급 실행."""

from __future__ import annotations

from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.video_analysis import VideoAnalysis
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.records_extractor import run_records_extraction


async def backfill_records_for_group(group: Group, *, limit: int = 500) -> dict:
    """그룹의 분석 완료 영상에 records 추출을 순차 실행. {processed} 반환."""
    await dpm.ensure_schema(group)
    async with dpm.group_session(group) as session:
        rows = (await session.execute(
            select(
                VideoAnalysis.video_pk, VideoAnalysis.one_line,
                VideoAnalysis.analysis_sections, VideoAnalysis.insights,
                VideoAnalysis.key_points, VideoAnalysis.entities, VideoAnalysis.sentiment,
            ).limit(limit)
        )).all()
    processed = 0
    for r in rows:
        analysis = {
            "one_line": r[1], "analysis_sections": r[2], "insights": r[3],
            "key_points": r[4], "entities": r[5], "sentiment": r[6],
        }
        await run_records_extraction(group=group, video_pk=r[0], analysis=analysis)
        processed += 1
    return {"processed": processed}
