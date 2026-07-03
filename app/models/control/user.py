"""app.users — 서비스 계정. role: admin | user, status: active | suspended."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": APP_SCHEMA}

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.plans.plan_id"), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
