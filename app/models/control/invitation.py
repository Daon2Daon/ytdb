"""app.invitations — 초대제 가입 토큰 (1회용, 만료 있음)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = {"schema": APP_SCHEMA}

    invite_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.plans.plan_id"), nullable=False
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    invited_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
