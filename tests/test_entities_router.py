"""병합 승인 큐 라우터 스모크(경로 등록 + 공개 wrapper)."""

from app.routers import entities as entities_router


def test_entities_routes_registered():
    paths = {r.path for r in entities_router.router.routes}
    assert "/api/groups/{slug}/entities/merge-candidates" in paths
    assert "/api/groups/{slug}/entities/{entity_pk}/merge" in paths
    assert "/api/groups/{slug}/entities/{entity_pk}/reject" in paths


def test_apply_merge_cluster_exported():
    from app.services.entity_service import apply_merge_cluster
    assert callable(apply_merge_cluster)
