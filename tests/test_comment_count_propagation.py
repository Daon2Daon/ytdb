"""videos.comment_count가 모든 등록·갱신 경로에서 채워지는지 검증한다.

한 경로라도 빠지면 그 경로로 들어온 영상은 신선도 배너
(freshness/cardState)가 영원히 뜨지 않는다 — current_comment_count가
NULL이면 프론트가 판단을 보류하기 때문이다.
"""

import inspect

from app.schemas.video import VideoDetail


def test_video_detail_exposes_comment_count():
    assert "comment_count" in VideoDetail.model_fields


def test_polling_insert_sets_comment_count():
    from app.services.monitor_service import MonitorService

    src = inspect.getsource(MonitorService.insert_group_videos)
    assert "comment_count=vm.comment_count" in src


def test_stats_refresh_updates_comment_count():
    from app.services.monitor_service import run_stats_refresh_once

    src = inspect.getsource(run_stats_refresh_once)
    assert "comment_count=cc" in src


def test_instant_insert_sets_comment_count():
    from app.routers import videos

    src = inspect.getsource(videos.instant_analyze_video)
    assert "comment_count=vm.comment_count" in src


def test_ensure_video_row_sets_comment_count():
    from app.routers import videos

    src = inspect.getsource(videos.ensure_video_row)
    assert "comment_count=vm.comment_count" in src
