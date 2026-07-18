"""Phase E-1 — 플랜 만료·자동 강등 (스펙 2026-07-15-phase-e1)."""

from datetime import datetime, timedelta, timezone


def test_user_model_has_expiry_columns():
    from app.models.control.user import User

    t = User.__table__
    assert t.c.plan_expires_at.nullable is True
    assert t.c.plan_expiry_notified_at.nullable is True


def test_plan_seeds_include_pro():
    from app.services.auth_service import PLAN_SEEDS

    pro = next(s for s in PLAN_SEEDS if s["slug"] == "pro")
    assert pro["is_default"] is False
    assert (pro["max_groups"], pro["max_channels_total"]) == (3, 30)
    assert (pro["max_analyses_per_day"], pro["max_video_minutes"]) == (100, 120)
    assert pro["min_poll_interval_min"] == 10
    # free가 여전히 유일한 기본 플랜
    assert [s["slug"] for s in PLAN_SEEDS if s["is_default"]] == ["free"]
