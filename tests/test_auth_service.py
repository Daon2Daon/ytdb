"""auth_service 순수 유틸 검증 (DB 불필요)."""

from app.services.auth_service import (
    PLAN_SEEDS,
    admin_bootstrap_email,
    generate_invite_token,
    hash_password,
    is_auth_enabled,
    set_users_exist,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123" and h.startswith("$argon2")
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_bad_hash_returns_false():
    assert verify_password("x", "not-a-hash") is False


def test_admin_bootstrap_email():
    assert admin_bootstrap_email("admin@example.com") == "admin@example.com"
    assert admin_bootstrap_email("admin") == "admin@local"
    assert admin_bootstrap_email("Admin") == "admin@local"


def test_invite_token_unique_and_urlsafe():
    tokens = {generate_invite_token() for _ in range(20)}
    assert len(tokens) == 20
    for t in tokens:
        assert len(t) >= 32
        assert all(c.isalnum() or c in "-_" for c in t)


def test_plan_seeds_have_default_free_and_unlimited():
    slugs = {p["slug"] for p in PLAN_SEEDS}
    assert slugs == {"free", "unlimited"}
    defaults = [p for p in PLAN_SEEDS if p["is_default"]]
    assert len(defaults) == 1 and defaults[0]["slug"] == "free"


def test_is_auth_enabled_matrix(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    set_users_exist(False)
    assert is_auth_enabled() is False  # 개발 모드
    set_users_exist(True)
    assert is_auth_enabled() is True  # 사용자 존재 → 항상 활성
    set_users_exist(False)
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "pw")
    assert is_auth_enabled() is True  # env 자격증명만 있어도 활성
    set_users_exist(False)
