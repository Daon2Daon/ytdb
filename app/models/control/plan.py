"""app.plans — 사용자 플랜(쿼터 정의). Phase A는 테이블·시드만, 강제는 Phase B."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = {"schema": APP_SCHEMA}

    plan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    max_groups: Mapped[int] = mapped_column(Integer, nullable=False)
    max_channels_total: Mapped[int] = mapped_column(Integer, nullable=False)
    max_analyses_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    max_video_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_cost_budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    min_poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
