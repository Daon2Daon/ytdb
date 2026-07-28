"""app.comment_analysis_runs — 사용자별 댓글 분석 크레딧 원장.

그룹 스키마는 서로 격리되어 있어 사용자별 월 집계를 데이터 평면에서 할 수 없다.
analysis_deliveries와 같은 이유로 제어 평면에 둔다.

analysis_deliveries와 달리 UNIQUE 제약이 없다 — 재분석은 실제 비용이 다시
발생하는 새 사건이므로 행이 쌓이는 것이 맞다.
group_id에 FK를 두지 않아 그룹 삭제 후에도 원장이 보존된다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class CommentAnalysisRun(Base):
    __tablename__ = "comment_analysis_runs"
    __table_args__ = (
        Index("comment_analysis_runs_user_created", "user_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    video_id: Mapped[str] = mapped_column(Text, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
