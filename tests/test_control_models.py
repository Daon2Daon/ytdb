"""제어 평면 신규 모델(plans/users/invitations)과 groups.owner_user_id 검증."""

from app.control_db import APP_SCHEMA, Base
from app.models.control.group import Group
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.user import User


def test_tables_registered_in_app_schema():
    tables = Base.metadata.tables
    for name in ("plans", "users", "invitations"):
        assert f"{APP_SCHEMA}.{name}" in tables


def test_user_columns():
    cols = {c.name for c in User.__table__.columns}
    assert {"user_id", "email", "password_hash", "display_name", "role",
            "status", "plan_id", "last_login_at", "created_at", "updated_at"} <= cols


def test_plan_columns_match_spec():
    cols = {c.name for c in Plan.__table__.columns}
    assert {"plan_id", "slug", "name", "max_groups", "max_channels_total",
            "max_analyses_per_day", "max_video_minutes",
            "monthly_cost_budget_usd", "min_poll_interval_min", "is_default"} <= cols


def test_invitation_columns():
    cols = {c.name for c in Invitation.__table__.columns}
    assert {"invite_id", "token", "plan_id", "memo", "invited_by",
            "expires_at", "used_by", "used_at", "created_at"} <= cols


def test_group_has_owner():
    assert "owner_user_id" in {c.name for c in Group.__table__.columns}


def test_b0b_tables_registered():
    """B-0b 테이블 3개가 Base.metadata에 app 스키마로 등록된다."""
    from app.models.control.channel_registry import ChannelRegistry
    from app.models.control.channel_subscription import ChannelSubscription
    from app.models.control.global_setting import GlobalSetting

    assert ChannelRegistry.__table__.schema == "app"
    assert ChannelSubscription.__table__.schema == "app"
    assert GlobalSetting.__table__.schema == "app"

    # 비정규화 컬럼은 NOT NULL (스펙 §2 — 동기화 시점에 유효값 해석 완료)
    sub = ChannelSubscription.__table__
    assert sub.c.poll_interval_min.nullable is False
    assert sub.c.window_hours.nullable is False
    # 복합 PK
    assert {c.name for c in sub.primary_key.columns} == {"channel_id", "group_id"}
    # 그룹 삭제 캐스케이드 백스톱
    fk_group = next(fk for fk in sub.c.group_id.foreign_keys)
    assert fk_group.ondelete == "CASCADE"

    # DB 레벨 DEFAULT (raw pg_insert 경로 보호 — 품질 리뷰 반영)
    assert ChannelRegistry.__table__.c.subscriber_groups.server_default is not None
    assert GlobalSetting.__table__.c.is_secret.server_default is not None


def test_user_limits_model_columns():
    from app.models.control.user_limit import UserLimit

    cols = {c.name for c in UserLimit.__table__.columns}
    assert cols == {
        "user_id", "max_groups", "max_channels_total", "max_analyses_per_day",
        "max_video_minutes", "monthly_cost_budget_usd", "min_poll_interval_min",
        "note", "updated_at",
    }
    # user_id 외 한도 컬럼은 전부 NULL 허용(NULL=플랜 값 사용)
    for name in ("max_groups", "max_channels_total", "max_analyses_per_day",
                 "max_video_minutes", "monthly_cost_budget_usd", "min_poll_interval_min"):
        assert UserLimit.__table__.columns[name].nullable is True


def test_analysis_delivery_unique_constraint():
    from sqlalchemy import UniqueConstraint

    from app.models.control.analysis_delivery import AnalysisDelivery

    uqs = [c for c in AnalysisDelivery.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert any(
        {col.name for col in uq.columns} == {"user_id", "cache_id"} for uq in uqs
    )


def test_ai_usage_model_columns():
    from app.models.control.ai_usage import AIUsage

    cols = {c.name for c in AIUsage.__table__.columns}
    assert cols == {
        "usage_id", "user_id", "group_id", "purpose", "model",
        "input_tokens", "output_tokens", "cost_usd", "video_pk", "created_at",
    }
    # user_id NULL = 시스템 몫(공유 캐시 분석). group_id는 FK 없음(원장 보존).
    assert AIUsage.__table__.columns["user_id"].nullable is True
    assert AIUsage.__table__.columns["group_id"].nullable is True
    assert AIUsage.__table__.columns["cost_usd"].nullable is True
    fk_cols = {fk.parent.name for fk in AIUsage.__table__.foreign_keys}
    assert fk_cols == {"user_id"}
