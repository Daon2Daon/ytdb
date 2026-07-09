"""app.analysis_deliveries — 사용자별 분석 전달 원장 (스펙 §2.9).

캐시 히트/미스 무관하게 "그룹에 분석이 전달된 사건"을 1행씩 기록한다.
Phase B의 max_analyses_per_day 쿼터 카운트와 향후 과금의 기반 데이터.
group_id에 FK를 두지 않아 그룹 삭제 후에도 원장이 보존된다.
같은 (user_id, cache_id) 재전달은 기록하지 않는다(UNIQUE).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AnalysisDelivery(Base):
    __tablename__ = "analysis_deliveries"
    __table_args__ = (
        Index("analysis_deliveries_user_created", "user_id", "created_at"),
        # 같은 사용자가 같은 캐시 분석을 재수신해도 원장 행이 늘지 않는다
        # (재분석은 캐시 복사일 뿐 새 가치가 아님 — 일일 쿼터 과카운트 방지, 스펙 §2.2).
        UniqueConstraint("user_id", "cache_id", name="uq_analysis_deliveries_user_cache"),
        {"schema": APP_SCHEMA},
    )

    delivery_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.analysis_cache.cache_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
