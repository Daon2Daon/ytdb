"""notification dispatch_scope 파싱/폴백 검증."""

from app.services.settings_manager import _normalize_dispatch_scope


def test_default_is_after_activation():
    assert _normalize_dispatch_scope(None) == "after_activation"
    assert _normalize_dispatch_scope("") == "after_activation"


def test_valid_values_pass_through():
    assert _normalize_dispatch_scope("after_activation") == "after_activation"
    assert _normalize_dispatch_scope("all") == "all"


def test_unknown_falls_back_to_after_activation():
    assert _normalize_dispatch_scope("garbage") == "after_activation"
