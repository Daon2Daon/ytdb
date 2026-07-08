"""app.global_settings — 전역 설정 최소 골격 (스펙 B-0b, Phase C에서 항목 추가).

그룹별 app.settings와 동일한 평문/암호문 이원 저장 패턴(value/value_enc).
B-0b 시드 키: youtube_api_key(시크릿), central_poll_floor_min.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    __table_args__ = {"schema": APP_SCHEMA}

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)       # 평문
    value_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # 암호문
    is_secret: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
