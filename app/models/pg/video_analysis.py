"""데이터 평면: video_analysis (videos와 1:1, 요약+상세 통합)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class VideoAnalysis(PgBase):
    __tablename__ = "video_analysis"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        primary_key=True,
    )

    # 요약
    one_line: Mapped[str] = mapped_column(Text, nullable=False, default="")
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_summary_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bullet_points: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    # 상세 분석
    full_analysis_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    insights: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    entities: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 모델/비용 메타
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
