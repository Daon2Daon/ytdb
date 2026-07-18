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


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _cand(expires_delta_days, notified=False):
    from app.services.plan_expiry_service import ExpiryCandidate

    return ExpiryCandidate(
        user_id=2, email="u@x.com",
        plan_expires_at=NOW + timedelta(days=expires_delta_days),
        plan_expiry_notified_at=NOW - timedelta(days=1) if notified else None,
    )


def test_classify_boundaries():
    from app.services.plan_expiry_service import classify

    assert classify(_cand(-0.01), NOW) == "demote"       # 만료 지남
    assert classify(_cand(3), NOW) == "notify"            # 7일 이내·미통지
    assert classify(_cand(3, notified=True), NOW) == "none"  # 이미 통지
    assert classify(_cand(8), NOW) == "none"              # 7일 초과
    assert classify(_cand(7), NOW) == "notify"            # 경계: 정확히 7일


async def test_run_once_demotes_and_notifies_with_isolation(monkeypatch):
    from app.services import plan_expiry_service as pes

    cands = [
        pes.ExpiryCandidate(10, "expired@x.com", NOW - timedelta(days=1), None),
        pes.ExpiryCandidate(11, "soon@x.com", NOW + timedelta(days=3), None),
        pes.ExpiryCandidate(12, "far@x.com", NOW + timedelta(days=8), None),
    ]
    actions = {"demoted": [], "notified": []}

    async def fake_load():
        return cands

    async def fake_demote(user_id):
        actions["demoted"].append(user_id)

    async def fake_mark(user_id):
        actions["notified"].append(user_id)

    async def fake_send(user_id, text):
        if user_id == 10:
            raise RuntimeError("텔레그램 실패")  # 알림 실패가 강등을 못 막는다

    monkeypatch.setattr(pes, "_load_candidates", fake_load)
    monkeypatch.setattr(pes, "_demote_user", fake_demote)
    monkeypatch.setattr(pes, "_mark_notified", fake_mark)
    monkeypatch.setattr(pes, "_send_user_telegram", fake_send)
    monkeypatch.setattr(pes, "_now", lambda: NOW)

    await pes.run_plan_expiry_once()
    assert actions["demoted"] == [10]     # 만료자만 강등 (알림 실패에도 강등 완료)
    assert actions["notified"] == [11]    # 임박자만 통지 마킹 (far=12 제외)
