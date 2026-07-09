"""캐시 선점(claim) 분기 검증. SQL 실행은 FakeSession으로 대체(실 SQL은 E2E에서)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.analysis_cache_service import (
    CACHE_STALE_PENDING_MINUTES,
    ClaimOutcome,
    claim_or_get,
)


class FakeRow:
    def __init__(self, cache_id=1, status="completed", analysis=None, created_at=None):
        self.cache_id = cache_id
        self.status = status
        self.analysis = analysis if analysis is not None else {"one_line": "x"}
        self.created_at = created_at or datetime.now(timezone.utc)


class FakeResult:
    def __init__(self, scalar=None, row=None, rowcount=0):
        self._scalar = scalar
        self._row = row
        self.rowcount = rowcount

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    """execute() 호출 순서대로 준비된 FakeResult를 돌려준다."""

    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, stmt):
        return self._results.pop(0)

    async def commit(self):
        self.committed = True


async def test_insert_wins_returns_claimed():
    # 1) INSERT ... RETURNING cache_id → 42 (선점 성공)
    fake = FakeSession([FakeResult(scalar=42)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=42, analysis=None)


async def test_conflict_completed_returns_hit():
    # 1) INSERT → None(충돌), 2) SELECT → completed 행
    row = FakeRow(cache_id=9, status="completed", analysis={"one_line": "요약"})
    fake = FakeSession([FakeResult(scalar=None), FakeResult(row=row)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "hit" and out.cache_id == 9 and out.analysis == {"one_line": "요약"}


async def test_conflict_fresh_pending_returns_in_progress():
    row = FakeRow(status="pending", created_at=datetime.now(timezone.utc))
    fake = FakeSession([FakeResult(scalar=None), FakeResult(row=row)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "in_progress"


async def test_conflict_stale_pending_reclaims():
    stale = datetime.now(timezone.utc) - timedelta(minutes=CACHE_STALE_PENDING_MINUTES + 5)
    row = FakeRow(cache_id=3, status="pending", created_at=stale)
    # 3) UPDATE(재클레임) → rowcount 1
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=1)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=3, analysis=None)


async def test_conflict_failed_reclaims():
    row = FakeRow(cache_id=5, status="failed")
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=1)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=5, analysis=None)


async def test_stale_pending_double_reclaim_second_loses():
    """동시 재클레임 레이스: 두 번째 워커는 created_at 가드로 rowcount 0 → in_progress."""
    stale = datetime.now(timezone.utc) - timedelta(minutes=CACHE_STALE_PENDING_MINUTES + 5)
    row = FakeRow(cache_id=3, status="pending", created_at=stale)
    # 첫 워커가 이미 재클레임해 created_at이 갱신됨 → 이 워커의 UPDATE는 rowcount 0
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=0)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "in_progress"


async def test_reclaim_lost_race_returns_in_progress():
    row = FakeRow(cache_id=5, status="failed")
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=0)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "in_progress"


async def test_record_delivery_is_conflict_free():
    """record_delivery가 ON CONFLICT DO NOTHING insert를 발행한다."""
    from app.services.analysis_cache_service import record_delivery

    captured = []

    class _S:
        async def execute(self, stmt):
            captured.append(stmt)

    await record_delivery(_S(), user_id=1, group_id=2, cache_id=3)
    assert len(captured) == 1
    sql = str(captured[0].compile(compile_kwargs={"literal_binds": False}))
    assert "ON CONFLICT" in sql
