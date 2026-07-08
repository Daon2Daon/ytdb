"""app.channel_subscriptions — channel_id → 구독 그룹 역방향 매핑 (스펙 B-0b).

스키마-per-그룹 구조에서 "이 채널을 누가 구독하나"를 그룹 스키마 스캔 없이
답하는 유일한 수단. poll_interval_min/window_hours는 동기화 시점에 해석
완료된 유효값(NULL 없음 — 그룹 기본값 해석은 동기화가 담당).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class ChannelSubscription(Base):
    __tablename__ = "channel_subscriptions"
    __table_args__ = (
        Index("channel_subscriptions_group", "group_id"),
        {"schema": APP_SCHEMA},
    )

    channel_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(f"{APP_SCHEMA}.channel_registry.channel_id"),
        primary_key=True,
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{APP_SCHEMA}.groups.group_id", ondelete="CASCADE"),
        primary_key=True,
    )
    poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False)
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False)
