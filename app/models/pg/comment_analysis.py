"""데이터 평면: comment_analyses (영상당 최신 댓글 반응 분석 1건).

댓글 원문은 전량 저장하지 않는다 — result JSONB 안에 카테고리별 대표 댓글
10개씩만 스냅샷한다. 재분석은 같은 행을 갱신하므로 UNIQUE(video_pk)가
동시 실행 방어선을 겸한다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, BigInteger, DateTime, ForeignKey, Integer, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class CommentAnalysis(PgBase):
    __tablename__ = "comment_analyses"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    analysis_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=STATUS_PENDING, server_default=f"'{STATUS_PENDING}'"
    )
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    positive_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negative_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neutral_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
