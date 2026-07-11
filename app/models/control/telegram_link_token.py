"""app.telegram_link_tokens — 딥링크 1회용 연결 토큰 (TTL 10분, 스펙 §1).

DB 저장이라 앱 재시작에도 유효. 만료 토큰은 발급 시 lazy 삭제.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class TelegramLinkToken(Base):
    __tablename__ = "telegram_link_tokens"
    __table_args__ = {"schema": APP_SCHEMA}

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
