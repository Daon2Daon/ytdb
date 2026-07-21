"""데이터 평면: analysis_records (한 행 = 한 사실). record_schema로 형태 정의."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class AnalysisRecord(PgBase):
    __tablename__ = "analysis_records"
    __table_args__ = (
        UniqueConstraint(
            "video_pk", "record_type", "position",
            name="ux_analysis_records_video_type_pos",
        ),
        Index("ix_analysis_records_type_entity", "record_type", "entity_name"),
        Index("ix_analysis_records_video", "video_pk"),
        {"schema": SCHEMA_TOKEN},
    )

    record_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        nullable=False,
    )
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_num: Mapped[Any | None] = mapped_column(Numeric, nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
