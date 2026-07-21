"""profile 라우터·부트스트랩 트리거 스모크 테스트(순수/모킹)."""

from __future__ import annotations

from app.routers import profile as profile_router


def test_profile_router_has_expected_routes():
    paths = {r.path for r in profile_router.router.routes}
    assert "/api/groups/{slug}/profile" in paths


def test_regenerate_route_registered():
    methods = set()
    for r in profile_router.router.routes:
        if r.path == "/api/groups/{slug}/profile/regenerate":
            methods |= r.methods
    assert "POST" in methods
