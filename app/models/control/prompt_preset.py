"""app.prompt_presets — 관리자가 만드는 분석/다이제스트 프롬프트 프리셋.

프리셋은 불변(immutable)이다: analysis_prompt/digest_prompt 본문은 생성 후 수정하지
않는다. 본문을 바꾸려면 새 프리셋을 만들고 구버전을 비활성화한다(is_active=false).
공유 분석 캐시(§2.9)의 캐시 키가 preset_id이므로, 본문이 바뀌면 캐시 정합성이 깨진다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class PromptPreset(Base):
    __tablename__ = "prompt_presets"
    __table_args__ = {"schema": APP_SCHEMA}

    preset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    digest_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
