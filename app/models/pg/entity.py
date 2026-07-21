"""데이터 평면: entities (자동 축적 엔티티 사전). 사용자 등록 대기 없음."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class Entity(PgBase):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("canonical_name", name="ux_entities_canonical"),
        {"schema": SCHEMA_TOKEN},
    )

    entity_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="auto", server_default="auto")
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
