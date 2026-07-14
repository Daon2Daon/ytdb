"""ensure_schema force + migrate_all_schemas 리포트 (스펙 §2)."""

from types import SimpleNamespace

import pytest

from app.services.db_engine import DataPlaneEngineManager, DBNotConfiguredError

GROUP = SimpleNamespace(group_id=1, slug="g1", schema_name="s1")


class Sentinel(Exception):
    pass


@pytest.fixture
def dpm(monkeypatch):
    m = DataPlaneEngineManager()

    async def fake_cfg(group):
        return SimpleNamespace(server_signature=lambda: "srv1")

    async def boom(cfg):
        raise Sentinel("DDL 경로 진입")

    monkeypatch.setattr(m, "_cfg", fake_cfg)
    monkeypatch.setattr(m, "_shared_engine", boom)
    return m


async def test_ensure_schema_cached_returns_early(dpm):
    dpm._initialized.add(("srv1", "s1"))
    await dpm.ensure_schema(GROUP)  # 캐시 히트 — DDL 경로 진입 안 함


async def test_ensure_schema_force_bypasses_cache(dpm):
    dpm._initialized.add(("srv1", "s1"))
    with pytest.raises(Sentinel):
        await dpm.ensure_schema(GROUP, force=True)  # 캐시 우회 — DDL 경로 진입
