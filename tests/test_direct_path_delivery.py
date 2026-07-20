"""직접 프롬프트 분석 경로의 전달 원장 기록 (monkeypatch, DB·LLM 불필요).

배경: '오늘 분석' 카운트와 일일 쿼터는 analysis_deliveries를 세는데,
기존에는 공유 캐시 경로에서만 기록돼 직접 프롬프트 분석이 집계에서 빠졌다.
"""

from types import SimpleNamespace

from app.models.control.analysis_delivery import AnalysisDelivery


def test_delivery_cache_id_is_nullable():
    """직접 경로는 캐시 행이 없으므로 cache_id NULL 기록을 허용해야 한다."""
    assert AnalysisDelivery.__table__.c.cache_id.nullable is True


async def test_record_delivery_safe_records_without_cache_id(monkeypatch):
    """cache_id=None(직접 경로)이어도 owner가 있으면 원장을 기록한다."""
    from app.services import monitor_service as ms

    recorded = {}

    async def _fake_record(user_id, group_id, cache_id):
        recorded.update(user_id=user_id, group_id=group_id, cache_id=cache_id)

    monkeypatch.setattr(ms, "record_delivery_for", _fake_record)
    group = SimpleNamespace(slug="g", group_id=7, owner_user_id=2)

    await ms._record_delivery_safe(group, None)
    assert recorded == {"user_id": 2, "group_id": 7, "cache_id": None}


async def test_record_delivery_safe_skips_ownerless_group(monkeypatch):
    """owner 미지정(레거시) 그룹은 귀속 대상이 없어 기록하지 않는다."""
    from app.services import monitor_service as ms

    called = []

    async def _fake_record(user_id, group_id, cache_id):
        called.append(1)

    monkeypatch.setattr(ms, "record_delivery_for", _fake_record)
    group = SimpleNamespace(slug="g", group_id=7, owner_user_id=None)

    await ms._record_delivery_safe(group, None)
    assert called == []


def test_direct_path_records_delivery_in_source():
    """직접 경로 성공 블록이 전달 원장을 기록하는지 배선을 검사한다.

    _run_analysis 전체 실행은 세션/파이프라인 목킹 비용이 과해,
    성공 블록(record_usage 직접 귀속 기록 이후)에 _record_delivery_safe
    호출이 존재하는지 소스 수준으로 확인한다.
    """
    import inspect

    from app.services import monitor_service as ms

    src = inspect.getsource(ms._run_analysis)
    # 직접 경로 성공 블록: owner 귀속 record_usage 다음에 전달 원장 기록이 와야 한다.
    usage_idx = src.index("user_id=group.owner_user_id")
    assert "_record_delivery_safe(group, None)" in src[usage_idx:], (
        "직접 경로 성공 시 _record_delivery_safe(group, None) 호출이 없습니다"
    )
