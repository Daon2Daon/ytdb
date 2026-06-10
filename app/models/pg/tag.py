"""데이터 평면: tags, video_tags (M:N)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase

# DB CHECK 제약(tags_tag_type_check)과 일치해야 함. 이 외 값은 INSERT 시 위반.
ALLOWED_TAG_TYPES = frozenset({"topic", "ticker", "person", "sector"})
DEFAULT_TAG_TYPE = "topic"


class Tag(PgBase):
    __tablename__ = "tags"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    tag_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tag_type: Mapped[str] = mapped_column(Text, nullable=False, default="topic")
    video_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoTag(PgBase):
    __tablename__ = "video_tags"
    __table_args__ = {"schema": SCHEMA_TOKEN}

    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.tags.tag_pk", ondelete="CASCADE"),
        primary_key=True,
    )
    weight: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
