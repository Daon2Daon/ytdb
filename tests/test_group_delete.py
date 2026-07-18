"""그룹 삭제: 자동 생성 스키마 판별 + 스키마 드롭 + 삭제 라우트 동작."""

import pytest

from app.routers.groups import is_auto_schema
from app.services.db_engine import DataPlaneEngineManager, DBNotConfiguredError


def test_is_auto_schema_matches_generated_pattern():
    # create_group이 만드는 형태: youtube_u{user_id}_{token_hex(3)}
    assert is_auto_schema("youtube_u1_a1b2c3") is True
    assert is_auto_schema("youtube_u42_00ff00") is True


def test_is_auto_schema_rejects_custom_schemas():
    assert is_auto_schema("youtube_invest") is False        # 레거시/관리자 커스텀
    assert is_auto_schema("youtube_u1_xyz") is False        # hex 아님
    assert is_auto_schema("youtube_u1_a1b2c3d4") is False   # hex 길이 초과
    assert is_auto_schema("youtube_ua_a1b2c3") is False     # user_id 숫자 아님
    assert is_auto_schema("public") is False


# ---- drop_schema ----


class _FakeConn:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))


class _FakeBegin:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        return _FakeBegin(self._conn)


class _FakeCfg:
    def server_signature(self) -> str:
        return "sig"


class _GroupRef:
    group_id = 7
    schema_name = "youtube_u1_a1b2c3"


async def test_drop_schema_executes_drop_and_clears_cache(monkeypatch):
    dpm = DataPlaneEngineManager()
    conn = _FakeConn()

    async def _cfg(group):
        return _FakeCfg()

    async def _shared(cfg):
        return _FakeEngine(conn)

    monkeypatch.setattr(dpm, "_cfg", _cfg)
    monkeypatch.setattr(dpm, "_shared_engine", _shared)
    dpm._initialized.add(("sig", "youtube_u1_a1b2c3"))

    await dpm.drop_schema(_GroupRef())

    assert any(
        'DROP SCHEMA IF EXISTS "youtube_u1_a1b2c3" CASCADE' in s for s in conn.statements
    )
    assert ("sig", "youtube_u1_a1b2c3") not in dpm._initialized


async def test_drop_schema_skips_when_db_not_configured(monkeypatch):
    dpm = DataPlaneEngineManager()

    async def _cfg(group):
        raise DBNotConfiguredError("no db")

    monkeypatch.setattr(dpm, "_cfg", _cfg)
    # 예외 없이 조용히 반환해야 한다(스키마가 만들어진 적 없음).
    await dpm.drop_schema(_GroupRef())
