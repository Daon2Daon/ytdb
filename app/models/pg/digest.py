"""데이터 평면: digests (다이제스트 결과)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Float, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class Digest(PgBase):
    __tablename__ = "digests"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    digest_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    period_type: Mapped[str] = mapped_column(Text, nullable=False, default="weekly")
    period_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    digest_config_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    share_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    share_visibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    sentiment_breakdown: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    top_tags: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    top_channels: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
