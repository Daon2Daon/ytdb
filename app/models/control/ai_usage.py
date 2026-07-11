"""app.ai_usage — AI 사용량 원장 (스펙 §2.4). "실제 LLM API 지불 금액"의 신뢰원.

user_id NULL = 시스템 몫(공유 캐시 분석 — 최초 트리거 사용자 귀속은 복불복 과금이라 금지).
사용자별 건수 카운트는 analysis_deliveries가 담당 — 역할 분리.
group_id는 FK 없음: 그룹 삭제 후에도 원장 보존.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text

from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ai_usage_user_created", "user_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    usage_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # 'analysis' | 'digest'
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    video_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
