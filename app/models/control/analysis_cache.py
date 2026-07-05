"""app.analysis_cache — 공유 분석 캐시 (스펙 §2.9).

캐시 키 = (video_id, preset_id, model). UNIQUE 제약이 동시 분석 방지 락을 겸한다:
INSERT ... ON CONFLICT DO NOTHING의 성공 여부로 분석 수행권을 선점한다.
status: pending(선점됨/분석 중) | completed(analysis 사용 가능) | failed(재클레임 가능).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"
    __table_args__ = (
        UniqueConstraint("video_id", "preset_id", "model", name="uq_analysis_cache_key"),
        {"schema": APP_SCHEMA},
    )

    cache_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(Text, nullable=False)  # YouTube 영상 ID
    preset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.prompt_presets.preset_id"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    analysis: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    # 토큰 수는 Phase C(사용량 원장)에서 배선. 현행 LLM 클라이언트는 usage 미노출.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
