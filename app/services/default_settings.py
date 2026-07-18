"""그룹 생성 시 자동 시드하는 추천 기본 설정.

사용자 편의를 위해 새 그룹에는 안전하게 추천할 수 있는 기본값을 미리
채워 둔다. 시크릿(API 키/비밀번호/봇 토큰)과 접속 식별 정보(host/dbname/
username/chat_ids)는 사용자가 직접 입력해야 하므로 시드하지 않는다.

값/타입은 settings_manager.get_* 의 기본값과 일치시킨다(단일 출처).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.analyzer import DEFAULT_ANALYSIS_PROMPT
from app.services.settings_types import PRESET_FULL
from app.services.settings_manager import get_settings_manager

# category -> [{key, value(str), value_type}]
# 주의: ai_gateway의 base_url/primary_model은 시드하지 않는다 — 그룹 명시값은
# resolve_ai_gateway의 전역 폴백보다 우선하므로, 시드하면 관리자가 전역 설정을
# 바꿔도 그룹들이 시드 시점 값에 영구 고정된다(2026-07-18 회귀에서 발견).
DEFAULT_GROUP_SETTINGS: dict[str, list[dict[str, Any]]] = {
    "ai_gateway": [
        {"key": "temperature", "value": "0.3", "value_type": "float"},
        {"key": "max_tokens", "value": "8192", "value_type": "int"},
    ],
    "database": [
        {"key": "port", "value": "5432", "value_type": "int"},
        {"key": "sslmode", "value": "prefer", "value_type": "string"},
    ],
    "polling": [
        {"key": "window_hours", "value": "24", "value_type": "int"},
        {"key": "default_channel_interval_min", "value": "720", "value_type": "int"},
        {"key": "max_concurrent_channels", "value": "5", "value_type": "int"},
        {"key": "pending_analysis_interval_min", "value": "12", "value_type": "int"},
        {"key": "max_concurrent_analyses", "value": "3", "value_type": "int"},
        {"key": "stats_refresh_days", "value": "30", "value_type": "int"},
    ],
    "notification": [
        {"key": "enabled", "value": "true", "value_type": "bool"},
        {"key": "parse_mode", "value": "HTML", "value_type": "string"},
        {"key": "send_mode", "value": "immediate", "value_type": "string"},
        {"key": "scheduled_max_per_run", "value": "5", "value_type": "int"},
        {"key": "wait_between_messages_sec", "value": "30", "value_type": "int"},
        {"key": "quiet_hours_enabled", "value": "false", "value_type": "bool"},
        {"key": "quiet_hours_start", "value": "22:00", "value_type": "string"},
        {"key": "quiet_hours_end", "value": "07:00", "value_type": "string"},
        {"key": "timezone", "value": "Asia/Seoul", "value_type": "string"},
        {"key": "low_confidence_threshold", "value": "0.5", "value_type": "float"},
        {"key": "message_template", "value": json.dumps(PRESET_FULL), "value_type": "json"},
        {"key": "notify_baseline_at", "value": "", "value_type": "string"},
        {"key": "dispatch_scope", "value": "after_activation", "value_type": "string"},
    ],
    "digest": [
        {"key": "configs", "value": "[]", "value_type": "json"},
        {"key": "share_link_enabled", "value": "true", "value_type": "bool"},
    ],
    "prompts": [
        {"key": "analysis_prompt", "value": DEFAULT_ANALYSIS_PROMPT, "value_type": "string"},
    ],
}


def _seed_items_for(category: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """카테고리 시드 항목을 CALL TIME 값으로 보정한 사본을 만든다(모듈 DEFAULTS 불변).

    notification.notify_baseline_at는 생성 시각(now)으로 채운다 — 신규 그룹은 생성
    이후 게시 영상만 자동 발송한다. dest 연결만으로 sendable해져도 baseline 부재로
    발송이 보류되는 온보딩 갭을 제거한다(과거 backlog flood 방지 취지는 유지).
    """
    if category != "notification":
        return items
    now_iso = datetime.now(timezone.utc).isoformat()
    return [
        {**it, "value": now_iso} if it.get("key") == "notify_baseline_at" else it
        for it in items
    ]


async def seed_default_settings(group_id: int) -> None:
    """그룹에 추천 기본값을 채운다(카테고리별 upsert)."""
    mgr = get_settings_manager()
    for category, items in DEFAULT_GROUP_SETTINGS.items():
        await mgr.set_values(group_id, category, _seed_items_for(category, items))
