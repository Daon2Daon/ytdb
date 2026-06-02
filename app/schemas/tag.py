"""태그 조회 출력 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_pk: int
    name: str
    tag_type: str
    video_count: int
    created_at: datetime
