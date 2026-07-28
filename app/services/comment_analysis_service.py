"""댓글 반응 분석 파이프라인: 수집 → 분류 → 인사이트.

분류는 200건 배치를 순차 호출한다. 병렬 호출은 게이트웨이 레이트리밋을
건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
배치 하나가 실패해도 그 배치만 중립 처리하고 계속 진행한다 —
전체 분석을 버리지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

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
