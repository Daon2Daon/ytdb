"""실패 영상 일괄 재분석 엔드포인트 라우트 등록 스모크."""

from app.main import app


def test_reset_failed_route_registered():
    paths = {r.path for r in app.routes}
    assert "/api/groups/{slug}/videos/reset-failed" in paths
