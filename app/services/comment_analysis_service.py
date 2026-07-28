"""댓글 반응 분석 파이프라인: 수집 → 분류 → 인사이트.

분류는 200건 배치를 순차 호출한다. 병렬 호출은 게이트웨이 레이트리밋을
건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
배치 하나가 실패해도 그 배치만 중립 처리하고 계속 진행한다 —
전체 분석을 버리지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from app.services.youtube_api import CommentMeta

LABEL_POSITIVE = "positive"
LABEL_NEGATIVE = "negative"
LABEL_NEUTRAL = "neutral"

_LABEL_MAP = {"p": LABEL_POSITIVE, "n": LABEL_NEGATIVE, "u": LABEL_NEUTRAL}

BATCH_SIZE = 200
TOP_COMMENTS_PER_CATEGORY = 10


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = "\n".join(
            line for line in t.splitlines() if not line.startswith("```")
        ).strip()
    return t


def parse_label_map(raw: str, batch_size: int) -> Dict[int, str]:
    """LLM 응답을 {배치내 인덱스: 라벨}로 변환한다.

    파싱 실패·인덱스 누락·알 수 없는 라벨은 모두 중립으로 폴백한다.
    반환 dict은 항상 0..batch_size-1 전체를 포함한다.
    """
    out: Dict[int, str] = {i: LABEL_NEUTRAL for i in range(batch_size)}
    try:
        parsed = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for key, value in parsed.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < batch_size:
            out[idx] = _LABEL_MAP.get(str(value).strip().lower(), LABEL_NEUTRAL)
    return out


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
