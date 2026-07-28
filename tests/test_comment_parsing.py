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
    got, ok = parse_label_map('{"0":"p","1":"n","2":"u"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE, 2: LABEL_NEUTRAL}
    assert ok is True


def test_strips_code_fence():
    raw = '```json\n{"0":"p","1":"n"}\n```'
    got, ok = parse_label_map(raw, batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE}
    assert ok is True


def test_missing_index_falls_back_to_neutral():
    got, ok = parse_label_map('{"0":"p"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL, 2: LABEL_NEUTRAL}
    # 일부라도 해석됐으면 배치 실패가 아니다.
    assert ok is True


def test_unknown_label_falls_back_to_neutral():
    got, ok = parse_label_map('{"0":"x","1":"p"}', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_POSITIVE}
    assert ok is True


def test_out_of_range_index_is_ignored():
    got, ok = parse_label_map('{"0":"p","9":"n"}', batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL}
    assert ok is True


def test_truncated_json_yields_all_neutral():
    got, ok = parse_label_map('{"0":"p","1":', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}
    assert ok is False


def test_empty_response_yields_all_neutral():
    got, ok = parse_label_map("", batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}
    assert ok is False


def test_full_word_labels_are_accepted():
    # 프롬프트는 p/n/u를 요구하지만 모델이 단어로 답하는 드리프트가 흔하다.
    got, ok = parse_label_map('{"0":"positive","1":"NEGATIVE","2":"neutral"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE, 2: LABEL_NEUTRAL}
    assert ok is True


def test_extracts_json_object_embedded_in_prose():
    raw = '분류 결과입니다:\n{"0":"p","1":"n"}\n이상입니다.'
    got, ok = parse_label_map(raw, batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE}
    assert ok is True


def test_extracts_json_when_text_follows_code_fence():
    raw = '```json\n{"0":"p","1":"n"}\n```\n이상입니다.'
    got, ok = parse_label_map(raw, batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE}
    assert ok is True


def test_json_array_is_reported_as_failure():
    # 위치 기반 해석은 오분류 위험이 있어 시도하지 않는다 — 실패로 보고한다.
    got, ok = parse_label_map('["p","n"]', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}
    assert ok is False


def test_no_resolvable_index_is_reported_as_failure():
    # JSON은 유효하지만 배치 인덱스로 쓸 수 있는 키가 하나도 없다.
    got, ok = parse_label_map('{"c0":"p","c1":"n"}', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}
    assert ok is False


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
