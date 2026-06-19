"""Digest 스케줄 설정 파싱·검증."""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.services.settings_types import (
    DigestScheduleConfig,
    MAX_DIGEST_CONFIGS,
    VALID_PERIOD_DAYS,
    VALID_SCHEDULE_DAYS,
)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_time(raw: str) -> tuple[int, int] | None:
    m = _TIME_RE.match((raw or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def normalize_schedule_config(raw: dict[str, Any], *, index: int) -> DigestScheduleConfig:
    cfg_id = str(raw.get("id") or "").strip() or str(uuid.uuid4())
    name = str(raw.get("name") or "").strip() or f"Digest {index + 1}"
    period_days = int(raw.get("period_days") or 7)
    if period_days not in VALID_PERIOD_DAYS:
        period_days = 7
    schedule_day = str(raw.get("schedule_day") or "sun").strip().lower()
    if schedule_day not in VALID_SCHEDULE_DAYS:
        schedule_day = "sun"
    schedule_time = str(raw.get("schedule_time") or "20:00").strip() or "20:00"
    if _parse_time(schedule_time) is None:
        schedule_time = "20:00"
    schedule_dom = int(raw.get("schedule_dom") or 1)
    schedule_dom = max(1, min(28, schedule_dom))
    timezone = str(raw.get("timezone") or "Asia/Seoul").strip() or "Asia/Seoul"
    return DigestScheduleConfig(
        id=cfg_id,
        name=name,
        enabled=bool(raw.get("enabled", False)),
        period_days=period_days,
        schedule_time=schedule_time,
        schedule_day=schedule_day,
        schedule_dom=schedule_dom,
        timezone=timezone,
        category=str(raw.get("category") or "").strip(),
        digest_prompt=str(raw.get("digest_prompt") or ""),
        telegram_enabled=bool(raw.get("telegram_enabled", False)),
    )


def parse_digest_configs(raw: Any) -> list[DigestScheduleConfig]:
    if not isinstance(raw, list):
        return []
    out: list[DigestScheduleConfig] = []
    for i, item in enumerate(raw[:MAX_DIGEST_CONFIGS]):
        if isinstance(item, dict):
            out.append(normalize_schedule_config(item, index=i))
    return out


def configs_to_json(configs: list[DigestScheduleConfig]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "enabled": c.enabled,
            "period_days": c.period_days,
            "schedule_time": c.schedule_time,
            "schedule_day": c.schedule_day,
            "schedule_dom": c.schedule_dom,
            "timezone": c.timezone,
            "category": c.category,
            "digest_prompt": c.digest_prompt,
            "telegram_enabled": c.telegram_enabled,
        }
        for c in configs[:MAX_DIGEST_CONFIGS]
    ]


def legacy_flat_to_config(d: dict[str, Any]) -> DigestScheduleConfig | None:
    """레거시 flat digest 키 → 단일 DigestScheduleConfig."""
    if not any(k in d for k in ("enabled", "period_weeks", "schedule_day", "schedule_time")):
        return None
    weeks = max(1, int(d.get("period_weeks") or 1))
    schedule_day = str(d.get("schedule_day") or "sun").strip().lower()
    if schedule_day not in VALID_SCHEDULE_DAYS:
        schedule_day = "sun"
    return DigestScheduleConfig(
        id="legacy",
        name="주간 리뷰",
        enabled=bool(d.get("enabled", False)),
        period_days=max(7, weeks * 7),
        schedule_day=schedule_day,
        schedule_time=str(d.get("schedule_time") or "20:00").strip() or "20:00",
        schedule_dom=1,
        timezone=str(d.get("timezone") or "Asia/Seoul").strip() or "Asia/Seoul",
        category=str(d.get("category") or "").strip(),
        digest_prompt="",
        telegram_enabled=bool(d.get("telegram_enabled", False)),
    )
