"""데이터 평면: job_logs (폴링/분석/알림 등 잡 실행 로그).

모태는 월별 파티션 테이블이지만, 신규 그룹에는 단순 테이블로 생성한다.
기존 스키마 어댑션 시에는 create_all checkfirst로 기존(파티션) 테이블을 건드리지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class JobLog(PgBase):
    __tablename__ = "job_logs"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    log_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    channel_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
