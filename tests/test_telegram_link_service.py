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


async def test_handle_update_binds_on_valid_start(monkeypatch):
    from app.services import telegram_link_service as tls

    calls = {}

    async def _fake_consume_in_session(token, chat_id, title):
        calls.update(token=token, chat_id=chat_id, title=title)
        return True

    sent = {}

    async def _fake_send(bot_token, chat_id, text):
        sent.update(chat_id=chat_id, text=text)

    monkeypatch.setattr(tls, "_consume_in_session", _fake_consume_in_session)
    monkeypatch.setattr(tls, "_send_bot_message", _fake_send)

    update = {"message": {
        "chat": {"id": 12345, "type": "private"},
        "from": {"first_name": "길동", "last_name": "홍", "username": "gildong"},
        "text": "/start tok123",
    }}
    await tls.handle_update(update, bot_token="BT")
    assert calls == {"token": "tok123", "chat_id": 12345, "title": "길동 홍"}
    assert "연결 완료" in sent["text"]


async def test_handle_update_ignores_non_private_and_non_start(monkeypatch):
    from app.services import telegram_link_service as tls

    async def _boom(*a, **k):
        raise AssertionError("호출되면 안 됨")

    monkeypatch.setattr(tls, "_consume_in_session", _boom)
    monkeypatch.setattr(tls, "_send_bot_message", _boom)

    await tls.handle_update({"message": {"chat": {"id": 1, "type": "group"}, "text": "/start t"}}, bot_token="BT")
    await tls.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "text": "안녕"}}, bot_token="BT")
    await tls.handle_update({"edited_message": {}}, bot_token="BT")


async def test_handle_update_replies_on_invalid_token(monkeypatch):
    from app.services import telegram_link_service as tls

    async def _fail(token, chat_id, title):
        return False

    sent = {}

    async def _fake_send(bot_token, chat_id, text):
        sent.update(text=text)

    monkeypatch.setattr(tls, "_consume_in_session", _fail)
    monkeypatch.setattr(tls, "_send_bot_message", _fake_send)
    await tls.handle_update({"message": {
        "chat": {"id": 9, "type": "private"}, "from": {"first_name": "x"}, "text": "/start bad",
    }}, bot_token="BT")
    assert "만료" in sent["text"]
