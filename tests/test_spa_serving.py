"""컷오버 후 서빙 라우팅 검증: / = React SPA, /legacy = vanilla, /api 미매칭 = 404."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_legacy_serves_vanilla():
    """구 vanilla UI는 /legacy 에서 계속 서빙된다(롤백 안전망)."""
    resp = client.get("/legacy")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


def test_api_unmatched_is_404_not_spa():
    """존재하지 않는 /api 경로는 SPA로 흡수되지 않고 404."""
    resp = client.get("/api/groups/__nope__/does-not-exist")
    assert resp.status_code == 404


def test_root_served_by_spa():
    """루트는 React SPA가 담당(빌드 전이면 503, 빌드 후면 200 — 어느 쪽이든 404 아님)."""
    resp = client.get("/")
    assert resp.status_code in (200, 503)


def test_client_route_falls_back_to_spa():
    """클라이언트 라우팅 경로(/g/...)는 catch-all로 index.html에 폴백(404 아님)."""
    resp = client.get("/g/some-group/videos")
    assert resp.status_code in (200, 503)
