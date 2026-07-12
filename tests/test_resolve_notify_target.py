"""발송 대상 3단계 해석 (설계 §5) — 기존 그룹 무중단이 제1원칙."""

from types import SimpleNamespace

from app.services.notify_service import resolve_notify_target
from app.services.settings_types import NotificationSettings

DEST = SimpleNamespace(dest_id=7, user_id=2, chat_id=999, is_active=True)


def _patch_db(monkeypatch, *, global_token="GBT", get_result=None, first_active=None):
    from app.services import notify_service as ns

    async def _tok():
        return global_token

    class _S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, model, pk):
            return get_result
        async def execute(self, stmt):
            class _R:
                def scalar_one_or_none(_self):
                    return first_active
            return _R()

    monkeypatch.setattr(ns, "get_global_telegram_bot_token", _tok)
    monkeypatch.setattr(ns, "_ctrl_sessionmaker", lambda: (lambda: _S()))


async def test_priority1_direct_settings_untouched(monkeypatch):
    _patch_db(monkeypatch)
    notif = NotificationSettings(bot_token="GROUPBT", chat_ids=["111"], dest_id=7)
    out = await resolve_notify_target(2, notif)
    assert out.bot_token == "GROUPBT" and out.chat_ids == ["111"]  # 기존 경로 그대로


async def test_priority2_dest_id(monkeypatch):
    _patch_db(monkeypatch, get_result=DEST)
    notif = NotificationSettings(dest_id=7)
    out = await resolve_notify_target(2, notif)
    assert out.bot_token == "GBT" and out.chat_ids == ["999"]
    assert out.is_sendable  # enabled 기본 True + 채워진 대상


async def test_priority3_first_active_fallback(monkeypatch):
    _patch_db(monkeypatch, get_result=None, first_active=DEST)
    notif = NotificationSettings()  # dest_id 미지정
    out = await resolve_notify_target(2, notif)
    assert out.chat_ids == ["999"]


async def test_unresolvable_returns_original(monkeypatch):
    _patch_db(monkeypatch, get_result=None, first_active=None)
    notif = NotificationSettings()
    out = await resolve_notify_target(2, notif)
    assert not out.is_sendable  # 대상 없음 — 발송 안 함 유지

    out2 = await resolve_notify_target(None, notif)  # owner 없음(레거시)
    assert not out2.is_sendable

    _patch_db(monkeypatch, global_token="", first_active=DEST)  # 전역 봇 미설정
    out3 = await resolve_notify_target(2, notif)
    assert not out3.is_sendable
