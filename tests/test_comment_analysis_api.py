"""댓글 분석 라우터 등록과 전량 분류 실패 판정.

라우트 등록 검사는 이 저장소의 라우터 테스트 컨벤션(test_*_routes_registered)을 따른다.
"""

from app.main import app
from app.services.comment_analysis_service import all_batches_failed


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
