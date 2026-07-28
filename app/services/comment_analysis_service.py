"""댓글 반응 분석 파이프라인: 수집 → 분류 → 인사이트.

분류는 200건 배치를 순차 호출한다. 병렬 호출은 게이트웨이 레이트리밋을
건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
배치 하나가 실패해도 그 배치만 중립 처리하고 계속 진행한다 —
전체 분석을 버리지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Tuple

from app.services.youtube_api import CommentMeta

LABEL_POSITIVE = "positive"
LABEL_NEGATIVE = "negative"
LABEL_NEUTRAL = "neutral"

# 프롬프트는 p/n/u를 요구하지만 모델이 전체 단어로 답하는 드리프트가 흔하다.
_LABEL_MAP = {
    "p": LABEL_POSITIVE,
    "n": LABEL_NEGATIVE,
    "u": LABEL_NEUTRAL,
    LABEL_POSITIVE: LABEL_POSITIVE,
    LABEL_NEGATIVE: LABEL_NEGATIVE,
    LABEL_NEUTRAL: LABEL_NEUTRAL,
}

BATCH_SIZE = 200
TOP_COMMENTS_PER_CATEGORY = 10


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = "\n".join(
            line for line in t.splitlines() if not line.startswith("```")
        ).strip()
    return t


def _extract_json_object(text: str) -> str:
    """설명문에 둘러싸인 JSON 객체를 꺼낸다. 못 찾으면 원문 그대로."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_label_map(raw: str, batch_size: int) -> Tuple[Dict[int, str], bool]:
    """LLM 응답을 ({배치내 인덱스: 라벨}, 해석 성공 여부)로 변환한다.

    반환 dict은 항상 0..batch_size-1 전체를 포함한다 — 파싱 실패·인덱스
    누락·알 수 없는 라벨은 모두 중립으로 폴백하므로 호출부에 결측 처리가 없다.

    두 번째 값이 False면 배치에서 라벨을 하나도 건지지 못했다는 뜻이다
    (응답이 JSON이 아니거나, 객체가 아니거나, 쓸 수 있는 인덱스 키가 없음).
    중립 폴백은 크래시를 막지만 그 자체로는 분류가 아니므로, 호출부가
    실패 배치로 집계해 partial 플래그에 반영할 수 있어야 한다.
    """
    out: Dict[int, str] = {i: LABEL_NEUTRAL for i in range(batch_size)}
    try:
        parsed = json.loads(_extract_json_object(_strip_fence(raw)))
    except (json.JSONDecodeError, TypeError):
        return out, False
    if not isinstance(parsed, dict):
        return out, False
    resolved = 0
    for key, value in parsed.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < batch_size:
            out[idx] = _LABEL_MAP.get(str(value).strip().lower(), LABEL_NEUTRAL)
            resolved += 1
    return out, resolved > 0


def pick_top_comments(
    comments: Iterable[CommentMeta], cap: int = TOP_COMMENTS_PER_CATEGORY
) -> List[Dict[str, Any]]:
    """좋아요순 상위 cap개를 저장용 dict로 변환한다.

    result JSONB에 들어가는 유일한 댓글 원문 — 전량은 저장하지 않는다.
    """
    ordered = sorted(comments, key=lambda c: c.like_count, reverse=True)[:cap]
    return [
        {
            "author": c.author,
            "text": c.text,
            "like_count": c.like_count,
            "published_at": c.published_at,
        }
        for c in ordered
    ]


# (프롬프트 본문, 배치 크기) → LLM 원문 응답
ClassifyCall = Callable[[str, int], Awaitable[str]]


def build_classify_prompt(base_prompt: str, batch: List[CommentMeta]) -> str:
    """번호가 매겨진 댓글 목록을 프롬프트에 붙인다."""
    lines = [f"{i}. {c.text}" for i, c in enumerate(batch)]
    return base_prompt + "\n".join(lines)


async def classify_comments(
    comments: List[CommentMeta],
    call: ClassifyCall,
    batch_size: int = BATCH_SIZE,
    base_prompt: str = "",
) -> Tuple[List[str], int]:
    """댓글 전체를 배치로 순차 분류한다. (라벨 리스트, 실패 배치 수) 반환.

    call은 프롬프트와 배치 크기를 받아 LLM 원문을 돌려주는 주입 함수 —
    테스트에서 게이트웨이 없이 파이프라인을 검증할 수 있다.
    base_prompt가 비면 코드 기본 프롬프트를 쓴다(그룹 설정 미지정 시).

    실패 배치는 전부 중립 처리하고 계속 진행한다 — 전체 분석을 버리지 않는다.
    실패는 두 종류이며 둘 다 집계한다: call이 예외를 던진 경우와, 응답이
    와도 라벨을 하나도 해석하지 못한 경우. 후자를 세지 않으면 "중립 100%"가
    정상 결과로 보고된다.
    """
    from app.services.comment_prompts import DEFAULT_CLASSIFY_PROMPT

    prompt_base = base_prompt or DEFAULT_CLASSIFY_PROMPT
    labels: List[str] = []
    failures = 0
    for start in range(0, len(comments), batch_size):
        batch = comments[start : start + batch_size]
        prompt = build_classify_prompt(prompt_base, batch)
        try:
            raw = await call(prompt, len(batch))
        except Exception as e:  # noqa: BLE001 — 배치 격리: 한 배치 실패가 전체를 깨지 않음
            print(f"[comment-analysis] 배치 분류 호출 실패(중립 처리): {e}")
            failures += 1
            label_map = {i: LABEL_NEUTRAL for i in range(len(batch))}
        else:
            label_map, ok = parse_label_map(raw, len(batch))
            if not ok:
                print(
                    "[comment-analysis] 배치 응답 해석 실패(중립 처리): "
                    f"{(raw or '')[:120]!r}"
                )
                failures += 1
        labels.extend(label_map[i] for i in range(len(batch)))
    return labels, failures


def group_by_label(
    comments: List[CommentMeta], labels: List[str]
) -> Dict[str, List[CommentMeta]]:
    """댓글을 라벨별로 분할한다. 라벨이 부족하면 남은 댓글은 중립."""
    grouped: Dict[str, List[CommentMeta]] = {
        LABEL_POSITIVE: [], LABEL_NEGATIVE: [], LABEL_NEUTRAL: [],
    }
    for i, c in enumerate(comments):
        label = labels[i] if i < len(labels) else LABEL_NEUTRAL
        grouped.get(label, grouped[LABEL_NEUTRAL]).append(c)
    return grouped
