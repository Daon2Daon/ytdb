"""댓글 분석 라우터 등록, 시작 권한 UPSERT, 전량 분류 실패 판정.

라우트 등록 검사는 이 저장소의 라우터 테스트 컨벤션(test_*_routes_registered)을 따른다.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from app.main import app
from app.routers.comment_analysis import build_claim_stmt
from app.services.comment_analysis_service import all_batches_failed


def _claim_sql() -> str:
    stmt = build_claim_stmt(1, 1000, datetime.now(timezone.utc))
    return str(stmt.compile(dialect=postgresql.dialect()))


def _claim_set_clause() -> str:
    return _claim_sql().split("DO UPDATE SET")[1].split("WHERE")[0]


def test_reclaim_refreshes_updated_at():
    """재분석 claim은 updated_at을 갱신해야 한다.

    갱신하지 않으면 updated_at은 직전 실행이 '끝난' 시각으로 남는다. 그러면
    직전 완료로부터 15분이 지난 영상은 재분석을 시작하는 순간 이미 stale로
    판정되어, 곧바로 들어온 두 번째 요청이 409 대신 claim에 다시 성공한다.
    백그라운드 작업이 둘 붙어 크레딧이 이중 차감되고 LLM·YouTube 유닛도 두 배로 든다.

    SQLAlchemy는 on_conflict_do_update의 set_에 Column.onupdate를 적용하지
    않으므로(Core update()와 달리) 여기서 명시해야 한다.
    """
    assert "updated_at" in _claim_set_clause()


def test_claim_keeps_both_running_guards():
    """진행중 검사와 고착 인수 조건이 둘 다 WHERE에 남아 있어야 한다."""
    where = _claim_sql().split("DO UPDATE SET")[1].split("WHERE")[1]
    assert "status !=" in where
    assert "updated_at <" in where


def _paths() -> set[str]:
    out: set[str] = set()
    stack = list(app.routes)
    while stack:
        r = stack.pop()
        p = getattr(r, "path", None)
        if isinstance(p, str):
            out.add(p)
        stack.extend(getattr(r, "routes", []) or [])
    return out


def test_comment_analysis_routes_registered():
    paths = _paths()
    assert "/api/groups/{slug}/videos/{video_pk}/comment-analysis" in paths
    assert "/api/groups/{slug}/comment-analyses" in paths
    assert "/api/groups/{slug}/comment-analysis/by-url" in paths


def test_comment_credits_route_registered():
    assert "/api/me/comment-credits" in _paths()


def test_all_batches_failed_true_when_every_batch_failed():
    # 1,000건 / 배치 200 = 5배치, 5배치 전부 실패 → 분류가 한 건도 안 됐다.
    assert all_batches_failed(failures=5, comment_count=1000, batch_size=200) is True


def test_all_batches_failed_false_when_some_succeeded():
    assert all_batches_failed(failures=4, comment_count=1000, batch_size=200) is False


def test_all_batches_failed_false_without_failures():
    assert all_batches_failed(failures=0, comment_count=1000, batch_size=200) is False


def test_all_batches_failed_handles_partial_last_batch():
    # 250건 = 200 + 50 → 2배치
    assert all_batches_failed(failures=2, comment_count=250, batch_size=200) is True
    assert all_batches_failed(failures=1, comment_count=250, batch_size=200) is False


def test_all_batches_failed_false_when_no_comments():
    # 댓글이 없으면 배치도 없다 — 0 == 0으로 참이 되지 않도록 방어한다.
    assert all_batches_failed(failures=0, comment_count=0, batch_size=200) is False
