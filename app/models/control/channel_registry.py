"""app.channel_registry — 전역 채널 레지스트리 (스펙 B-0b).

중앙 폴러가 채널당 1회 폴링하기 위한 신뢰원. 구독 관계는
app.channel_subscriptions가 보관하며 subscriber_groups는 참고용 캐시
(동기화 지점에서 COUNT 재계산 — 증감 누적 드리프트 방지).
구독 0이어도 행은 유지한다(이력 보존, due 쿼리 join에서 자연 제외).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class ChannelRegistry(Base):
    __tablename__ = "channel_registry"
    __table_args__ = {"schema": APP_SCHEMA}

    channel_id: Mapped[str] = mapped_column(Text, primary_key=True)  # YouTube 채널 ID
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_playlist_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_video_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscriber_groups: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
