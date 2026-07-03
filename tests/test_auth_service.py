"""auth_service 순수 유틸 검증 (DB 불필요)."""

from app.services.auth_service import (
    admin_bootstrap_email,
    generate_invite_token,
    hash_password,
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
