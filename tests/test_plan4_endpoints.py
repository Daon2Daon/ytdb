from app.main import app
from app.routers.videos import AnalyzeNowRequest, NotifyRequest, VideoNotifyResponse


def test_analyze_now_request_optional_prompt():
    assert AnalyzeNowRequest().custom_prompt is None
    assert AnalyzeNowRequest(custom_prompt="x").custom_prompt == "x"


def test_notify_request_default_force_false():
    assert NotifyRequest().force is False


def test_notify_response_shape():
    r = VideoNotifyResponse(success=True, message="ok")
    assert r.success is True and r.notified_at is None


def test_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/groups/{slug}/videos/{video_pk}/notify" in paths
    assert "/api/groups/{slug}/videos/{video_pk}/analyze-now" in paths
