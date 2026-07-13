"""전역 설정 접근자/폴백 검증. SQL은 FakeSession으로 대체(실 SQL은 E2E)."""

from types import SimpleNamespace

import pytest

from app.services import global_settings as gs


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._results.pop(0)

    async def commit(self):
        pass


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(Fernet.generate_key())


async def test_get_global_plain():
    row = SimpleNamespace(key="central_poll_floor_min", value="10", value_enc=None, is_secret=False)
    out = await gs.get_global(FakeSession([FakeResult(row)]), "central_poll_floor_min")
    assert out == "10"


async def test_get_global_missing_returns_none():
    out = await gs.get_global(FakeSession([FakeResult(None)]), "youtube_api_key")
    assert out is None


async def test_get_global_secret_decrypts(monkeypatch):
    f = _fernet()
    monkeypatch.setattr(gs, "_get_fernet", lambda: f)
    row = SimpleNamespace(
        key="youtube_api_key", value=None,
        value_enc=f.encrypt(b"AIza-secret"), is_secret=True,
    )
    out = await gs.get_global(FakeSession([FakeResult(row)]), "youtube_api_key")
    assert out == "AIza-secret"


async def test_set_global_secret_requires_fernet(monkeypatch):
    from app.services.settings_manager import SettingsSecretError

    monkeypatch.setattr(gs, "_get_fernet", lambda: None)
    with pytest.raises(SettingsSecretError):
        await gs.set_global(FakeSession([]), "youtube_api_key", "AIza-x")


async def test_get_central_poll_floor_min_default_and_clamp():
    # 행 없음 → 기본 10
    assert await gs.get_central_poll_floor_min(FakeSession([FakeResult(None)])) == 10
    # 비정상 값(0 이하/비숫자) → 기본 10
    bad = SimpleNamespace(key="central_poll_floor_min", value="abc", value_enc=None, is_secret=False)
    assert await gs.get_central_poll_floor_min(FakeSession([FakeResult(bad)])) == 10


async def test_resolve_youtube_key_prefers_group_key(monkeypatch):
    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="group-key")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )
    assert await gs.resolve_youtube_key(1) == "group-key"


async def test_resolve_youtube_key_falls_back_to_system(monkeypatch):
    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def fake_system_key():
        return "system-key"

    monkeypatch.setattr(gs, "get_system_youtube_key", fake_system_key)
    assert await gs.resolve_youtube_key(1) == "system-key"


async def test_pick_seed_key_prefers_admin_group_with_key():
    """admin 소유 그룹 중 polling 키가 있는 첫 그룹의 키를 고른다 (순수 판정 함수)."""
    from app.services.global_settings import pick_bootstrap_youtube_key

    groups = [
        SimpleNamespace(group_id=1),
        SimpleNamespace(group_id=2),
    ]
    keys = {1: "", 2: "admin-key"}

    async def get_polling(group_id):
        return SimpleNamespace(youtube_api_key=keys[group_id])

    out = await pick_bootstrap_youtube_key(groups, get_polling)
    assert out == "admin-key"


async def test_pick_seed_key_none_when_no_keys():
    from app.services.global_settings import pick_bootstrap_youtube_key

    async def get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")

    assert await pick_bootstrap_youtube_key([SimpleNamespace(group_id=1)], get_polling) is None


async def test_bootstrap_seed_survives_missing_fernet(monkeypatch):
    """평문 그룹 키 + FERNET_KEY 부재 조합에서 SettingsSecretError가 부팅 경로 밖으로
    전파되지 않는다 (방어 가드). 시드 실패는 skip일 뿐 부팅을 막지 않는다."""
    from app.services.settings_manager import SettingsSecretError

    class _BootSession:
        """async CM + begin() CM + 그룹 조회용 execute 최소 구현."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def begin(self):
            return self

        async def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(gs, "get_sessionmaker", lambda: (lambda: _BootSession()))
    monkeypatch.setattr(
        gs, "get_settings_manager", lambda: SimpleNamespace(get_polling=None)
    )

    async def fake_get_global(session, key):
        return None  # 시스템 키 미설정 → 시드 경로 진입

    async def fake_pick(groups, get_polling):
        return "plain-group-key"  # 평문 그룹 키 발견

    async def fake_set_global(session, key, value):
        raise SettingsSecretError("시크릿을 저장하려면 FERNET_KEY가 필요합니다.")

    monkeypatch.setattr(gs, "get_global", fake_get_global)
    monkeypatch.setattr(gs, "pick_bootstrap_youtube_key", fake_pick)
    monkeypatch.setattr(gs, "set_global", fake_set_global)

    # 가드가 있으면 예외 없이 리턴, 없으면 SettingsSecretError가 테스트를 실패시킨다.
    await gs.bootstrap_global_settings()


def test_ai_global_keys_registered():
    from app.services.global_settings import (
        GLOBAL_AI_API_KEY,
        GLOBAL_AI_BASE_URL,
        GLOBAL_AI_DIGEST_MODEL,
        GLOBAL_AI_MODEL_PRICES,
        GLOBAL_AI_PRIMARY_MODEL,
        SECRET_KEYS,
    )

    assert GLOBAL_AI_API_KEY in SECRET_KEYS          # 암호화 저장
    assert GLOBAL_AI_MODEL_PRICES not in SECRET_KEYS # 단가표는 평문 JSON
    assert GLOBAL_AI_BASE_URL == "ai_base_url"
    assert GLOBAL_AI_PRIMARY_MODEL == "ai_primary_model"
    assert GLOBAL_AI_DIGEST_MODEL == "ai_digest_model"


async def test_resolve_ai_gateway_group_overrides_global(monkeypatch):
    """그룹 명시값 > 전역 > 코드 기본값."""
    from app.services import global_settings as gs

    async def _typed(group_id, category):
        # 그룹은 base_url만 명시
        return {"base_url": "http://group:4000", "api_key": "", "primary_model": ""}

    class _Mgr:
        get_typed = staticmethod(_typed)

    monkeypatch.setattr(gs, "get_settings_manager", lambda: _Mgr())

    async def _global(session, key):
        return {
            "ai_base_url": "http://global:4000",
            "ai_api_key": "gk",
            "ai_primary_model": "gemini/global-model",
            "ai_digest_model": "",
        }.get(key)

    monkeypatch.setattr(gs, "get_global", _global)

    class _S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    monkeypatch.setattr(gs, "get_sessionmaker", lambda: (lambda: _S()))

    ai = await gs.resolve_ai_gateway(7)
    assert ai.base_url == "http://group:4000"        # 그룹 명시값 우선
    assert ai.api_key == "gk"                        # 전역 폴백
    assert ai.primary_model == "gemini/global-model" # 전역 폴백
    assert ai.digest_model == ""                     # 전역도 빈값 → 기본값(빈값)


def test_get_ai_model_prices_parsing():
    from app.services.global_settings import _parse_model_prices

    assert _parse_model_prices('{"gemini/": {"input": 0.1, "output": 0.4}}') == {
        "gemini/": {"input": 0.1, "output": 0.4}
    }
    assert _parse_model_prices("") == {}
    assert _parse_model_prices(None) == {}
    assert _parse_model_prices("not json") == {}
    assert _parse_model_prices('["list"]') == {}


def test_telegram_bot_token_key_registered():
    from app.services.global_settings import GLOBAL_TELEGRAM_BOT_TOKEN, SECRET_KEYS

    assert GLOBAL_TELEGRAM_BOT_TOKEN == "telegram_bot_token"
    assert GLOBAL_TELEGRAM_BOT_TOKEN in SECRET_KEYS  # Fernet 암호화 저장


async def test_get_youtube_daily_quota_default_and_clamp():
    from types import SimpleNamespace as NS

    # 행 없음 → 기본 10000
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(None)])) == 10000
    # 비정상(비숫자/0 이하) → 기본
    bad = NS(key="youtube_daily_quota", value="abc", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(bad)])) == 10000
    zero = NS(key="youtube_daily_quota", value="0", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(zero)])) == 10000
    # 정상값
    ok = NS(key="youtube_daily_quota", value="50000", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(ok)])) == 50000
