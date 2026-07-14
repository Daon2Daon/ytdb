"""app.yt_quota_usage — YouTube API 쿼터 원장 (스펙 D-2 §1.1).

키 원문은 저장하지 않는다 — SHA-256 앞 12자 지문(key_fp)만. usage_date는
PT(America/Los_Angeles) 자정 기준: Google 실제 쿼터 리셋 시점과 일치.
날짜가 바뀌면 새 행이 시작되므로 별도 리셋 잡 불필요.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class YtQuotaUsage(Base):
    __tablename__ = "yt_quota_usage"
    __table_args__ = {"schema": APP_SCHEMA}

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    key_fp: Mapped[str] = mapped_column(Text, primary_key=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
