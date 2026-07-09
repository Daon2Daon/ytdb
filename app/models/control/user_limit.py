"""app.user_limits — 관리자의 사용자별 한도 오버라이드 (스펙 §2.3).

모든 한도 컬럼 NULL 허용 — NULL이면 플랜 값 사용(COALESCE는 quota_service가 담당).
monthly_cost_budget_usd는 Phase C에서 강제 — 스키마만 선반영.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class UserLimit(Base):
    __tablename__ = "user_limits"
    __table_args__ = {"schema": APP_SCHEMA}

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_groups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_channels_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_analyses_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_video_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_cost_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    min_poll_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
