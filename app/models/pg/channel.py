"""데이터 평면: channels (그룹 내 모니터링 채널).

그룹 경계는 스키마이므로 group_id 컬럼을 두지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class Channel(PgBase):
    __tablename__ = "channels"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    channel_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    channel_name: Mapped[str] = mapped_column(Text, nullable=False)
    channel_handle: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_playlist_id: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False, default=720)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_video_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
