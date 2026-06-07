"""그룹 데이터 평면 job_logs 기록 헬퍼.

job_logs는 그룹별 스키마에 있으므로, 그룹 스키마로 바인딩된 세션을 만드는
콜러블(make_session)을 받아 독립 트랜잭션으로 한 행을 INSERT한다.
(메인 작업 트랜잭션이 롤백돼도 로그는 남도록 분리한다.)
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.job_log import JobLog

JOB_TYPE_CHANNEL_POLL = "channel_poll"
JOB_TYPE_VIDEO_ANALYZE = "video_analyze"
JOB_TYPE_GATEWAY_HEALTH = "gateway_health"
JOB_TYPE_NOTIFY = "notify"
JOB_TYPE_STATS = "stats"

STATUS_SUCCESS = "success"
STATUS_FAIL = "failed"
STATUS_SKIP = "skipped"

MakeSession = Callable[[], AsyncSession]


async def write_job_log(
    make_session: MakeSession,
    job_type: str,
    status: str,
    message: Optional[str] = None,
    duration_ms: Optional[int] = None,
    channel_pk: Optional[int] = None,
    video_pk: Optional[int] = None,
) -> None:
    try:
        async with make_session() as session:
            async with session.begin():
                session.add(
                    JobLog(
                        job_type=job_type,
                        channel_pk=channel_pk,
                        video_pk=video_pk,
                        status=status,
                        message=(message or "")[:500] or None,
                        duration_ms=duration_ms,
                    )
                )
    except Exception as e:
        print(f"job_log 기록 실패 ({job_type}): {e}")


class JobTimer:
    """경과 시간 측정 컨텍스트 매니저."""

    def __init__(self) -> None:
        self._start = 0.0
        self.elapsed_ms = 0

    def __enter__(self) -> "JobTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_) -> None:
        self.elapsed_ms = int((time.monotonic() - self._start) * 1000)
