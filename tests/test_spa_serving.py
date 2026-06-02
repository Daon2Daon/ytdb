from fastapi.testclient import TestClient

from app.main import app


def test_api_route_not_captured_by_spa():
    """존재하지 않는 /api 경로는 SPA로 흡수되지 않고 404여야 한다."""
    client = TestClient(app)
    resp = client.get("/api/groups/__nope__/does-not-exist")
    assert resp.status_code == 404


def test_legacy_root_still_served():
    """기존 vanilla 진입점(/)이 살아있어야 한다."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()
