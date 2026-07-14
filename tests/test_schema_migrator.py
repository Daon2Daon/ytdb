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


async def test_migrate_all_schemas_mixed_report(monkeypatch):
    from app.services import schema_migrator as sm

    groups = [
        SimpleNamespace(group_id=1, slug="ok1", schema_name="s1"),
        SimpleNamespace(group_id=2, slug="nodb", schema_name="s2"),
        SimpleNamespace(group_id=3, slug="boom", schema_name="s3"),
        SimpleNamespace(group_id=4, slug="ok2", schema_name="s4"),
    ]

    async def fake_all_groups():
        return groups

    async def fake_ensure(group, *, force=False):
        assert force is True
        if group.slug == "nodb":
            raise DBNotConfiguredError("no db")
        if group.slug == "boom":
            raise RuntimeError("ALTER 실패")

    monkeypatch.setattr(sm, "_all_groups", fake_all_groups)
    monkeypatch.setattr(sm.dpm, "ensure_schema", fake_ensure)

    results = await sm.migrate_all_schemas()
    by_slug = {r.slug: r for r in results}
    assert len(results) == 4  # 중간 실패에도 전 그룹 순회 (그룹 단위 격리)
    assert by_slug["ok1"].status == "ok" and by_slug["ok1"].error is None
    assert by_slug["nodb"].status == "skipped"
    assert by_slug["boom"].status == "failed" and "ALTER 실패" in by_slug["boom"].error
    assert by_slug["ok2"].status == "ok"
    assert all(r.duration_ms >= 0 for r in results)


def test_summarize_counts():
    from app.services.schema_migrator import GroupMigrationResult, summarize

    results = [
        GroupMigrationResult(1, "a", "s1", "ok", None, 1),
        GroupMigrationResult(2, "b", "s2", "failed", "x", 1),
        GroupMigrationResult(3, "c", "s3", "skipped", None, 1),
        GroupMigrationResult(4, "d", "s4", "ok", None, 1),
    ]
    assert summarize(results) == {"ok": 2, "failed": 1, "skipped": 1}
