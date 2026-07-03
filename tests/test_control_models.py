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
