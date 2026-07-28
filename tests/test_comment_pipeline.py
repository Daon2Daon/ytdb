"""분류·집계 파이프라인 테스트 — LLM 호출은 스텁으로 대체한다."""

from app.services.comment_analysis_service import (
    LABEL_NEGATIVE,
    LABEL_NEUTRAL,
    LABEL_POSITIVE,
    classify_comments,
    group_by_label,
)
from app.services.youtube_api import CommentMeta


def _c(idx: int, likes: int = 0) -> CommentMeta:
    return CommentMeta(
        comment_id=f"c{idx}", author=f"u{idx}", text=f"t{idx}",
        like_count=likes, published_at="2026-07-20T10:00:00Z",
    )


async def test_classify_splits_into_batches_and_merges():
    seen_sizes: list[int] = []

    async def fake_call(prompt_text: str, batch_size: int) -> str:
        seen_sizes.append(batch_size)
        return "{" + ",".join(f'"{i}":"p"' for i in range(batch_size)) + "}"

    comments = [_c(i) for i in range(450)]
    labels, failures = await classify_comments(comments, fake_call, batch_size=200)

    assert seen_sizes == [200, 200, 50]
    assert len(labels) == 450
    assert all(v == LABEL_POSITIVE for v in labels)
    assert failures == 0


async def test_failed_batch_becomes_neutral_and_counts():
    async def fake_call(prompt_text: str, batch_size: int) -> str:
        if batch_size == 200:
            raise RuntimeError("gateway down")
        return "{" + ",".join(f'"{i}":"n"' for i in range(batch_size)) + "}"

    comments = [_c(i) for i in range(250)]
    labels, failures = await classify_comments(comments, fake_call, batch_size=200)

    assert failures == 1
    assert labels[:200] == [LABEL_NEUTRAL] * 200
    assert labels[200:] == [LABEL_NEGATIVE] * 50


async def test_unparseable_response_counts_as_failure():
    """예외가 아니라 해석 불가 응답도 실패 배치로 집계된다.

    parse_label_map이 중립으로 폴백만 하고 조용히 넘어가면 batch_failures가
    0으로 남아 "중립 100%"가 정상 결과로 보고된다.
    """
    async def fake_call(prompt_text: str, batch_size: int) -> str:
        if batch_size == 200:
            return "죄송합니다. 분류할 수 없습니다."
        return "{" + ",".join(f'"{i}":"p"' for i in range(batch_size)) + "}"

    comments = [_c(i) for i in range(250)]
    labels, failures = await classify_comments(comments, fake_call, batch_size=200)

    assert failures == 1
    assert labels[:200] == [LABEL_NEUTRAL] * 200
    assert labels[200:] == [LABEL_POSITIVE] * 50


async def test_empty_comments_returns_empty():
    async def fake_call(prompt_text: str, batch_size: int) -> str:
        raise AssertionError("호출되면 안 됨")

    labels, failures = await classify_comments([], fake_call, batch_size=200)
    assert labels == []
    assert failures == 0


def test_group_by_label_partitions_all_comments():
    comments = [_c(0), _c(1), _c(2)]
    labels = [LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_POSITIVE]
    grouped = group_by_label(comments, labels)

    assert [c.comment_id for c in grouped[LABEL_POSITIVE]] == ["c0", "c2"]
    assert [c.comment_id for c in grouped[LABEL_NEGATIVE]] == ["c1"]
    assert grouped[LABEL_NEUTRAL] == []


def test_group_by_label_tolerates_length_mismatch():
    # 라벨이 부족하면 남은 댓글은 중립으로 처리한다.
    comments = [_c(0), _c(1), _c(2)]
    grouped = group_by_label(comments, [LABEL_POSITIVE])
    assert len(grouped[LABEL_POSITIVE]) == 1
    assert len(grouped[LABEL_NEUTRAL]) == 2
