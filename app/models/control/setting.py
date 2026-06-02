"""app.settings — 그룹별 설정 (category/key, 시크릿은 Fernet 암호화)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("group_id", "category", "key", name="uq_settings_group_category_key"),
        {"schema": APP_SCHEMA},
    )

    setting_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{APP_SCHEMA}.groups.group_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # database / ai_gateway / prompts / polling / notification / digest
    category: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    # 평문 값 (is_secret=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 암호화 값 (is_secret=True)
    value_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    value_type: Mapped[str] = mapped_column(Text, nullable=False, default="string")
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
