"""그룹 baseline 게이트 적용 판정 검증.

scheduled+all 조합에서만 게이트를 끈다(False). 그 외는 모두 적용(True).
"""

from app.services.notify_service import _should_apply_group_baseline


def test_immediate_after_activation_applies():
    assert _should_apply_group_baseline("immediate", "after_activation") is True


def test_immediate_all_still_applies():
    assert _should_apply_group_baseline("immediate", "all") is True


def test_scheduled_after_activation_applies():
    assert _should_apply_group_baseline("scheduled", "after_activation") is True


def test_scheduled_all_skips():
    assert _should_apply_group_baseline("scheduled", "all") is False
