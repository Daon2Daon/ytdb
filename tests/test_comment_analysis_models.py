"""댓글 분석 제어 평면 모델 구조 테스트 (DB 연결 불필요 — 메타데이터만 검사)."""

from app.control_db import APP_SCHEMA
from app.models.control.comment_analysis_run import CommentAnalysisRun
from app.models.control.plan import Plan
from app.models.control.user_limit import UserLimit
from app.models.pg.base import SCHEMA_TOKEN
from app.models.pg.comment_analysis import CommentAnalysis
from app.models.pg.video import Video


def test_run_table_name_and_schema():
    assert CommentAnalysisRun.__tablename__ == "comment_analysis_runs"
    assert CommentAnalysisRun.__table__.schema == APP_SCHEMA


def test_run_has_required_columns():
    cols = set(CommentAnalysisRun.__table__.columns.keys())
    assert {
        "run_id", "user_id", "group_id", "video_id",
        "credits", "requested_limit", "comment_count", "created_at",
    } <= cols


def test_group_id_has_no_foreign_key():
    # 그룹 삭제 후에도 원장이 보존되도록 FK를 걸지 않는다.
    assert len(CommentAnalysisRun.__table__.c.group_id.foreign_keys) == 0


def test_user_created_index_exists():
    names = {ix.name for ix in CommentAnalysisRun.__table__.indexes}
    assert "comment_analysis_runs_user_created" in names


def test_plan_has_comment_quota_columns():
    cols = Plan.__table__.columns
    assert cols["max_comment_analyses_per_month"].nullable is False
    assert cols["max_comments_per_analysis"].nullable is False


def test_user_limit_comment_columns_are_nullable():
    # NULL = 플랜 값 사용 (COALESCE는 quota_service 담당)
    cols = UserLimit.__table__.columns
    assert cols["max_comment_analyses_per_month"].nullable is True
    assert cols["max_comments_per_analysis"].nullable is True


def test_comment_analysis_uses_schema_token():
    assert CommentAnalysis.__tablename__ == "comment_analyses"
    assert CommentAnalysis.__table__.schema == SCHEMA_TOKEN


def test_comment_analysis_video_pk_is_unique():
    # 영상당 최신 1건. 재분석은 덮어쓰기이며, 이 제약이 동시 실행 방어선을 겸한다.
    col = CommentAnalysis.__table__.c.video_pk
    assert col.unique is True
    assert len(col.foreign_keys) == 1


def test_comment_analysis_has_required_columns():
    cols = set(CommentAnalysis.__table__.columns.keys())
    assert {
        "analysis_id", "video_pk", "status", "requested_limit", "fetched_count",
        "total_count", "positive_count", "negative_count", "neutral_count",
        "result", "model", "partial", "error", "analyzed_at",
    } <= cols


def test_video_has_comment_count():
    assert "comment_count" in Video.__table__.columns
    assert Video.__table__.c.comment_count.nullable is True
