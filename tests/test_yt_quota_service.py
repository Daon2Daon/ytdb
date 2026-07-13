"""yt_quota_usage 모델·서비스 검증. SQL은 FakeSession, 실 SQL은 E2E."""

from app.control_db import APP_SCHEMA


def test_yt_quota_usage_model_shape():
    from app.models.control.yt_quota_usage import YtQuotaUsage

    t = YtQuotaUsage.__table__
    assert t.schema == APP_SCHEMA
    assert t.name == "yt_quota_usage"
    # 복합 PK (usage_date, key_fp) — 키별 카운트 (스펙 D1)
    assert {c.name for c in t.primary_key.columns} == {"usage_date", "key_fp"}
    assert t.c.units.nullable is False


def test_model_registered_in_control_metadata():
    # ensure_control_schema의 임포트 목록에 등록돼 create_all 대상이어야 한다
    import app.models.control.yt_quota_usage  # noqa: F401
    from app.control_db import Base

    assert f"{APP_SCHEMA}.yt_quota_usage" in Base.metadata.tables


from datetime import datetime, timezone

from app.services import yt_quota_service as yq


def test_key_fingerprint_deterministic_and_short():
    fp = yq.key_fingerprint("AIza-example-key")
    assert fp == yq.key_fingerprint("AIza-example-key")
    assert len(fp) == 12
    assert fp != yq.key_fingerprint("AIza-other-key")
    # 원문이 지문에 노출되지 않음
    assert "AIza" not in fp


def test_pt_today_crosses_date_line():
    # UTC 07-13 06:00 = PT 07-12 23:00 (PDT, UTC-7) → PT 날짜는 아직 07-12
    now = datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)
    assert yq.pt_today(now).isoformat() == "2026-07-12"
    # UTC 07-13 08:00 = PT 07-13 01:00 → 07-13
    now = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    assert yq.pt_today(now).isoformat() == "2026-07-13"


async def test_make_recorder_swallows_db_failure(monkeypatch):
    # DB가 완전히 죽어도 recorder는 예외를 던지지 않는다 (스펙 D5 best-effort)
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(yq, "get_sessionmaker", boom)
    rec = yq.make_recorder("AIza-x")
    await rec(3)  # 예외 없이 통과해야 함


async def test_units_today_returns_zero_when_no_row():
    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def execute(self, stmt):
            return FakeResult()

    assert await yq.units_today(FakeSession(), "abc123def456") == 0
