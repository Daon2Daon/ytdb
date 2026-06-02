"""설정 입출력 스키마."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

ValueType = Literal["string", "int", "float", "bool", "json"]


class SettingItem(BaseModel):
    key: str
    # 저장 시 입력값. 응답 시 시크릿은 마스킹된 문자열.
    value: Optional[Any] = None
    value_type: ValueType = "string"
    is_secret: bool = False
    description: Optional[str] = None


class SettingsUpdate(BaseModel):
    items: list[SettingItem]
