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
