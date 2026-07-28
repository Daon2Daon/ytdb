"""분류 응답 파서 테스트 — LLM 응답의 실패 모드를 방어한다."""

from app.services.comment_analysis_service import (
    LABEL_NEGATIVE,
    LABEL_NEUTRAL,
    LABEL_POSITIVE,
    parse_label_map,
    pick_top_comments,
)
from app.services.youtube_api import CommentMeta


def _c(idx: int, likes: int = 0) -> CommentMeta:
    return CommentMeta(
        comment_id=f"c{idx}", author=f"u{idx}", text=f"t{idx}",
        like_count=likes, published_at="2026-07-20T10:00:00Z",
    )


def test_parses_compact_map():
    got = parse_label_map('{"0":"p","1":"n","2":"u"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE, 2: LABEL_NEUTRAL}


def test_strips_code_fence():
    raw = '```json\n{"0":"p","1":"n"}\n```'
    assert parse_label_map(raw, batch_size=2) == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE}


def test_missing_index_falls_back_to_neutral():
    got = parse_label_map('{"0":"p"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL, 2: LABEL_NEUTRAL}


def test_unknown_label_falls_back_to_neutral():
    got = parse_label_map('{"0":"x","1":"p"}', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_POSITIVE}


def test_out_of_range_index_is_ignored():
    got = parse_label_map('{"0":"p","9":"n"}', batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL}


def test_truncated_json_yields_all_neutral():
    got = parse_label_map('{"0":"p","1":', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}


def test_empty_response_yields_all_neutral():
    assert parse_label_map("", batch_size=2) == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}


def test_pick_top_comments_sorts_by_likes_desc_and_caps():
    comments = [_c(i, likes=i) for i in range(15)]
    top = pick_top_comments(comments, 10)
    assert len(top) == 10
    assert top[0]["like_count"] == 14
    assert top[0]["author"] == "u14"
    assert top[-1]["like_count"] == 5


def test_pick_top_comments_handles_fewer_than_cap():
    top = pick_top_comments([_c(1, 5), _c(2, 3)], 10)
    assert len(top) == 2
    assert set(top[0].keys()) == {"author", "text", "like_count", "published_at"}
