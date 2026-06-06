"""발송 기준선 스탬프 판정(순수 로직) 검증."""

from app.services.notify_service import (
    _needs_baseline_backfill,
    _should_stamp_on_save,
)


def test_stamp_on_false_to_true():
    assert _should_stamp_on_save(before_sendable=False, after_sendable=True) is True


def test_no_stamp_when_already_sendable():
    assert _should_stamp_on_save(before_sendable=True, after_sendable=True) is False


def test_no_stamp_when_becomes_unsendable():
    assert _should_stamp_on_save(before_sendable=True, after_sendable=False) is False


def test_no_stamp_when_stays_unsendable():
    assert _should_stamp_on_save(before_sendable=False, after_sendable=False) is False


def test_backfill_when_sendable_and_no_baseline():
    assert _needs_baseline_backfill(sendable=True, baseline=object()) is False
    assert _needs_baseline_backfill(sendable=True, baseline=None) is True
    assert _needs_baseline_backfill(sendable=False, baseline=None) is False
