"""그룹 생성 시 자동 시드하는 추천 기본 설정.

사용자 편의를 위해 새 그룹에는 안전하게 추천할 수 있는 기본값을 미리
채워 둔다. 시크릿(API 키/비밀번호/봇 토큰)과 접속 식별 정보(host/dbname/
username/chat_ids)는 사용자가 직접 입력해야 하므로 시드하지 않는다.

값/타입은 settings_manager.get_* 의 기본값과 일치시킨다(단일 출처).
"""

from __future__ import annotations

from typing import Any

from app.services.analyzer import DEFAULT_ANALYSIS_PROMPT
from app.services.settings_manager import get_settings_manager

# category -> [{key, value(str), value_type}]
DEFAULT_GROUP_SETTINGS: dict[str, list[dict[str, Any]]] = {
    "ai_gateway": [
        {"key": "base_url", "value": "http://litellm:4000", "value_type": "string"},
        {"key": "primary_model", "value": "gemini/gemini-2.5-flash", "value_type": "string"},
        {"key": "fallback_model", "value": "gemini/gemini-2.5-flash", "value_type": "string"},
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
        {"key": "message_detail", "value": "full", "value_type": "string"},
    ],
    "digest": [
        {"key": "enabled", "value": "false", "value_type": "bool"},
        {"key": "period_weeks", "value": "1", "value_type": "int"},
        {"key": "schedule_day", "value": "sun", "value_type": "string"},
        {"key": "schedule_time", "value": "20:00", "value_type": "string"},
        {"key": "timezone", "value": "Asia/Seoul", "value_type": "string"},
        {"key": "telegram_enabled", "value": "false", "value_type": "bool"},
    ],
    "prompts": [
        {"key": "analysis_prompt", "value": DEFAULT_ANALYSIS_PROMPT, "value_type": "string"},
    ],
}


async def seed_default_settings(group_id: int) -> None:
    """그룹에 추천 기본값을 채운다(카테고리별 upsert)."""
    mgr = get_settings_manager()
    for category, items in DEFAULT_GROUP_SETTINGS.items():
        await mgr.set_values(group_id, category, items)
