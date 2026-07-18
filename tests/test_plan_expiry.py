"""Phase E-1 — 플랜 만료·자동 강등 (스펙 2026-07-15-phase-e1)."""

from datetime import datetime, timedelta, timezone


def test_user_model_has_expiry_columns():
    from app.models.control.user import User

    t = User.__table__
    assert t.c.plan_expires_at.nullable is True
    assert t.c.plan_expiry_notified_at.nullable is True
