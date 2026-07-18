"""그룹 삭제: 자동 생성 스키마 판별 + 스키마 드롭 + 삭제 라우트 동작."""

import pytest
from fastapi.testclient import TestClient

import app.routers.groups as groups_router
from app.control_db import get_session
from app.main import app
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user
from app.routers.groups import is_auto_schema
from app.services.auth_service import set_users_exist
from app.services.db_engine import (
    DataPlaneEngineManager,
    DBNotConfiguredError,
    data_plane_engine_manager,
)


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


# ---- DELETE /api/groups/{slug} ----

ALICE = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="user")
BOB = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


def _make_group(schema_name: str, owner: int | None = 1) -> Group:
    from datetime import datetime, timezone

    g = Group()
    g.group_id, g.slug, g.name, g.schema_name = 1, "g1", "그룹1", schema_name
    g.is_active, g.owner_user_id, g.description = True, owner, None
    g.created_at = g.updated_at = datetime.now(timezone.utc)
    return g


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DeleteFakeSession:
    """delete_group 경로용: get_group_or_404의 execute 1회 + delete/commit."""

    def __init__(self, group):
        self._group = group
        self.deleted = []
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._group)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class _Recorder:
    def __init__(self):
        self.drop_calls: list[str] = []
        self.unsub_calls: list[int] = []


def _setup(monkeypatch, group, user) -> tuple[TestClient, _DeleteFakeSession, _Recorder]:
    set_users_exist(True)
    rec = _Recorder()

    async def _user_dep():
        return user

    fake = _DeleteFakeSession(group)

    async def _session_dep():
        yield fake

    async def _fake_drop(g):
        rec.drop_calls.append(g.schema_name)

    async def _fake_unsub(session, group_id):
        rec.unsub_calls.append(group_id)

    app.dependency_overrides[require_user] = _user_dep
    app.dependency_overrides[get_session] = _session_dep
    monkeypatch.setattr(data_plane_engine_manager, "drop_schema", _fake_drop)
    monkeypatch.setattr(groups_router, "remove_group_subscriptions", _fake_unsub)
    return TestClient(app, raise_server_exceptions=False), fake, rec


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_delete_auto_schema_group_drops_schema(monkeypatch):
    group = _make_group("youtube_u1_a1b2c3", owner=1)
    client, fake, rec = _setup(monkeypatch, group, ALICE)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 204
    assert rec.drop_calls == ["youtube_u1_a1b2c3"]
    assert rec.unsub_calls == [1]
    assert fake.deleted == [group]
    assert fake.committed is True


def test_delete_custom_schema_group_keeps_schema(monkeypatch):
    group = _make_group("youtube_invest", owner=1)
    client, fake, rec = _setup(monkeypatch, group, ALICE)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 204
    assert rec.drop_calls == []            # 커스텀 스키마는 보존
    assert fake.deleted == [group]


def test_delete_stranger_group_404(monkeypatch):
    group = _make_group("youtube_u1_a1b2c3", owner=1)
    client, fake, rec = _setup(monkeypatch, group, BOB)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 404
    assert rec.drop_calls == []
    assert fake.deleted == []
