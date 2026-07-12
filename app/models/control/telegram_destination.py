"""app.telegram_destinations — 공용 봇 연결 대상 (스펙 §2.7, D-1은 private만).

사용자당 여러 destination 가능(재연결은 UNIQUE(user_id, chat_id) upsert).
chat_kind는 그룹채팅방 확장 대비 컬럼만 선반영('private' 고정).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class TelegramDestination(Base):
    __tablename__ = "telegram_destinations"
    __table_args__ = (
        UniqueConstraint("user_id", "chat_id", name="uq_telegram_destinations_user_chat"),
        {"schema": APP_SCHEMA},
    )

    dest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"), nullable=False
    )
    chat_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'private'"))
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # DM: 텔레그램 표시 이름
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
