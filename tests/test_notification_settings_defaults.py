"""NotificationSettings 신규 필드 기본값/하위호환 검증."""

from app.services.settings_types import NotificationSettings


def test_defaults():
    n = NotificationSettings()
    assert n.send_mode == "immediate"
    assert n.scheduled_times == []
    assert n.scheduled_max_per_run == 5
    assert n.wait_between_messages_sec == 30
    assert n.quiet_hours_enabled is False
    assert n.quiet_hours_start == "22:00"
    assert n.quiet_hours_end == "07:00"
    assert n.timezone == "Asia/Seoul"
    assert n.low_confidence_threshold == 0.5
    assert n.message_detail == "full"


def test_is_sendable_unchanged():
    # 기존 의미 유지: enabled + bot_token + chat_ids
    assert NotificationSettings().is_sendable is False
    n = NotificationSettings(enabled=True, bot_token="t", chat_ids=["1"])
    assert n.is_sendable is True
