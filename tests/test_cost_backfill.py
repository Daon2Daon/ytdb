"""NULL 비용 소급 계산(backfill) — 단가 등록 전 기록된 원장 행 복구."""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")

PRICES = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 0.50},
    "gemini-3.1": {"input": 1.0, "output": 2.0},
}


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


# ── 단가 해석 (compute_cost_usd와 동일한 최장 prefix 규칙) ──────────────────


def test_resolve_price_longest_prefix_wins():
    from app.services.ai_usage_service import resolve_price_for_model

    got = resolve_price_for_model("gemini-3.1-flash-lite", PRICES)
    assert got == (Decimal("0.25"), Decimal("0.50"))


def test_resolve_price_prefix_fallback():
    from app.services.ai_usage_service import resolve_price_for_model

    assert resolve_price_for_model("gemini-3.1-pro", PRICES) == (
        Decimal("1.0"), Decimal("2.0"),
    )


def test_resolve_price_no_match_returns_none():
    from app.services.ai_usage_service import resolve_price_for_model

    assert resolve_price_for_model("claude-opus", PRICES) is None
    assert resolve_price_for_model("gemini-3.1-flash-lite", {}) is None


def test_resolve_price_malformed_entry_returns_none():
    from app.services.ai_usage_service import resolve_price_for_model

    assert resolve_price_for_model("m", {"m": {"input": 1.0}}) is None
    assert resolve_price_for_model("m", {"m": "oops"}) is None


# ── 관리자 엔드포인트 배선 ───────────────────────────────────────────────────


def test_backfill_route_registered():
    assert "/api/admin/usage/backfill-costs" in {r.path for r in app.routes}


def test_backfill_non_admin_403():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u
    c = TestClient(app, raise_server_exceptions=False)
    assert c.post("/api/admin/usage/backfill-costs").status_code == 403


def test_backfill_response_shape(monkeypatch):
    async def _a():
        return ADMIN
    app.dependency_overrides[require_user] = _a

    async def _fake_backfill(session):
        return 5, 2

    monkeypatch.setattr("app.routers.admin.backfill_null_costs", _fake_backfill)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/admin/usage/backfill-costs")
    assert r.status_code == 200
    assert r.json() == {"updated": 5, "remaining_null": 2}
