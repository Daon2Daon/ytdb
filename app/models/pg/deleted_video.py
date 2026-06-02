"""데이터 평면: deleted_videos (사용자 삭제 영상 블록리스트)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class DeletedVideo(PgBase):
    __tablename__ = "deleted_videos"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
