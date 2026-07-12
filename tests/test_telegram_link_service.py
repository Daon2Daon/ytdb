"""텔레그램 연결 서비스 단위 테스트 (DB·네트워크 불필요 부분)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.telegram_link_service import (
    LINK_TOKEN_TTL_SEC,
    _token_valid,
    build_deep_link,
    parse_start_command,
)


def test_build_deep_link():
    assert build_deep_link("my_bot", "abc123") == "https://t.me/my_bot?start=abc123"


def test_parse_start_command():
    assert parse_start_command("/start abc123") == "abc123"
    assert parse_start_command("/start   abc123  ") == "abc123"
    assert parse_start_command("/start") == ""          # 맨손 /start
    assert parse_start_command("/start@my_bot tok") == "tok"  # 그룹형 접미 허용
    assert parse_start_command("hello") is None          # /start 아님
    assert parse_start_command("") is None


def test_token_valid():
    now = datetime.now(timezone.utc)
    ok = SimpleNamespace(used_at=None, expires_at=now + timedelta(minutes=5))
    used = SimpleNamespace(used_at=now, expires_at=now + timedelta(minutes=5))
    expired = SimpleNamespace(used_at=None, expires_at=now - timedelta(seconds=1))
    assert _token_valid(ok, now) is True
    assert _token_valid(used, now) is False
    assert _token_valid(expired, now) is False
    assert _token_valid(None, now) is False


def test_ttl_constant():
    assert LINK_TOKEN_TTL_SEC == 600
