"""app.groups — 모니터링 그룹 정의."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = {"schema": APP_SCHEMA}

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 잡 ID/스키마 접미사용 식별자 (예: invest)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 데이터 평면 스키마명 (예: youtube_invest)
    schema_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 소유자. NULL은 마이그레이션 이전 상태(관리자만 접근 가능). ON DELETE는 두지 않는다
    # (사용자 삭제 전 그룹 정리를 강제).
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
