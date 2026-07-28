"""댓글 반응 분석 파이프라인: 수집 → 분류 → 인사이트.

분류는 200건 배치를 순차 호출한다. 병렬 호출은 게이트웨이 레이트리밋을
건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
배치 하나가 실패해도 그 배치만 중립 처리하고 계속 진행한다 —
전체 분석을 버리지 않는다.
"""

from __future__ import annotations

import json
import math
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


def all_batches_failed(failures: int, comment_count: int, batch_size: int) -> bool:
    """모든 배치가 실패했는가 — 분류가 한 건도 이뤄지지 않았다는 뜻.

    이 경우 결과는 '중립 100%'가 되어 사용자에게 무가치하므로 크레딧을 환급한다.
    댓글이 0건이면 배치도 없으므로 0 == 0으로 참이 되지 않게 막는다.
    """
    if comment_count <= 0 or failures <= 0:
        return False
    return failures >= math.ceil(comment_count / batch_size)


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


def build_insight_prompt(
    base_prompt: str, grouped: Dict[str, List[CommentMeta]], cap: int = 40
) -> str:
    """카테고리별 좋아요순 상위 댓글만 추려 인사이트 프롬프트를 만든다.

    전량을 다시 보내면 입력 토큰이 두 배가 되므로 상위 cap개만 보낸다.
    """
    parts: List[str] = [base_prompt]
    for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL):
        items = sorted(grouped[label], key=lambda c: c.like_count, reverse=True)[:cap]
        parts.append(f"\n[{label}] ({len(grouped[label])}건)")
        if not items:
            parts.append("(없음)")
        else:
            parts.extend(f"- {c.text}" for c in items)
    return "\n".join(parts)


def parse_insight_response(raw: str) -> Dict[str, Dict[str, Any]]:
    """인사이트 응답을 카테고리별 dict로 변환한다. 실패 시 빈 구조."""
    empty = {"summary": "", "key_points": [], "insights": ""}
    out = {
        LABEL_POSITIVE: dict(empty),
        LABEL_NEGATIVE: dict(empty),
        LABEL_NEUTRAL: dict(empty),
    }
    try:
        parsed = json.loads(_extract_json_object(_strip_fence(raw)))
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL):
        entry = parsed.get(label)
        if not isinstance(entry, dict):
            continue
        kp = entry.get("key_points")
        out[label] = {
            "summary": str(entry.get("summary") or ""),
            "key_points": [str(x) for x in kp] if isinstance(kp, list) else [],
            "insights": str(entry.get("insights") or ""),
        }
    return out


async def run_comment_analysis(
    group, video_pk: int, video_id: str, user_id: int, requested_limit: int
) -> None:
    """BackgroundTasks 진입점. 예외를 밖으로 던지지 않는다.

    크레딧은 수집 완료 후에 확정 기록하고, LLM 실패 시 환급(원장 행 삭제)한다.
    실행되지 않은 분석에 크레딧을 물리지 않기 위해서다.
    """
    from dataclasses import replace
    from datetime import datetime, timezone

    from sqlalchemy import delete, select, update

    from app.control_db import get_sessionmaker
    from app.models.control.comment_analysis_run import CommentAnalysisRun
    from app.models.pg.comment_analysis import (
        STATUS_DONE, STATUS_FAILED, CommentAnalysis,
    )
    from app.models.pg.video import Video
    from app.services.ai_usage_service import record_usage
    from app.services.comment_prompts import DEFAULT_INSIGHT_PROMPT
    from app.services.db_engine import data_plane_engine_manager as dpm
    from app.services.global_settings import resolve_ai_gateway, resolve_youtube_key
    from app.services.llm_client import LiteLLMClient
    from app.services.quota_service import (
        QuotaExceeded, check_comment_analysis_quota, credits_for,
    )
    from app.services.settings_manager import get_settings_manager
    from app.services.yt_quota_service import make_recorder
    from app.services.youtube_api import YouTubeAPIClient

    run_id: int | None = None

    async def _fail(message: str) -> None:
        if run_id is not None:
            async with get_sessionmaker()() as s:
                async with s.begin():
                    await s.execute(
                        delete(CommentAnalysisRun).where(
                            CommentAnalysisRun.run_id == run_id
                        )
                    )
        async with dpm.group_session(group) as s:
            async with s.begin():
                await s.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(status=STATUS_FAILED, error=message[:2000])
                )

    try:
        # 1) 수집 — 이 단계 실패는 원장 기록 이전이므로 자연히 무차감이다.
        polling = await get_settings_manager().get_polling(group.group_id)
        api_key = await resolve_youtube_key(group.group_id)
        if not api_key:
            await _fail("YouTube API 키가 없습니다.")
            return
        polling = replace(polling, youtube_api_key=api_key)
        api = YouTubeAPIClient(polling, recorder=make_recorder(api_key))
        try:
            comments = await api.list_comment_threads(video_id, requested_limit)
        finally:
            await api.aclose()

        if not comments:
            await _fail("분석할 댓글이 없습니다.")
            return

        # 2) 크레딧 확정 — 요청 시점 검사와의 경합을 닫기 위해 같은 트랜잭션에서 재검사.
        credits = credits_for(requested_limit)
        async with get_sessionmaker()() as s:
            async with s.begin():
                try:
                    await check_comment_analysis_quota(s, user_id, requested_limit)
                except QuotaExceeded as e:
                    await _fail(e.detail)
                    return
                row = CommentAnalysisRun(
                    user_id=user_id,
                    group_id=group.group_id,
                    video_id=video_id,
                    credits=credits,
                    requested_limit=requested_limit,
                    comment_count=len(comments),
                )
                s.add(row)
                await s.flush()
                run_id = row.run_id

        # 3) 분류 + 인사이트
        # resolve_ai_gateway는 그룹값 → 전역 → 기본값 순으로 폴백한다. 저장소의
        # 다른 AI 소비자(analyzer/digest/monitor 등)가 전부 이 경로를 쓴다 —
        # settings_manager.get_ai_gateway는 그룹 스코프만 읽어 전역을 무시한다.
        gateway = await resolve_ai_gateway(group.group_id)
        prompts = await get_settings_manager().get_prompts(group.group_id)
        model = gateway.primary_model
        client = LiteLLMClient(gateway)
        totals = {"input": 0, "output": 0}

        async def _call(prompt_text: str, _batch_size: int) -> str:
            res = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt_text}],
                response_format={"type": "json_object"},
                temperature=gateway.temperature,
            )
            totals["input"] += res.input_tokens or 0
            totals["output"] += res.output_tokens or 0
            return res.content

        try:
            labels, failures = await classify_comments(
                comments, _call, base_prompt=prompts.comment_analysis_prompt
            )
            grouped = group_by_label(comments, labels)
            if all_batches_failed(failures, len(comments), BATCH_SIZE):
                # 분류가 한 건도 안 됐다 — '중립 100%' 결과는 무가치하므로
                # 인사이트 호출로 비용을 더 쓰지 않고 환급한다.
                raise RuntimeError(
                    f"댓글 분류에 모두 실패했습니다({failures}개 배치). 잠시 후 다시 시도해 주세요."
                )
            insight_raw = await _call(
                build_insight_prompt(DEFAULT_INSIGHT_PROMPT, grouped), 0
            )
            insights = parse_insight_response(insight_raw)
        finally:
            await client.aclose()
            await record_usage(
                user_id=user_id,
                group_id=group.group_id,
                purpose="comment_analysis",
                model=model,
                input_tokens=totals["input"],
                output_tokens=totals["output"],
                video_pk=video_pk,
            )

        # 4) 저장
        result = {
            "categories": {
                label: {
                    **insights[label],
                    "top_comments": pick_top_comments(grouped[label]),
                }
                for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL)
            },
            "order": "relevance",
            "batch_failures": failures,
        }
        async with dpm.group_session(group) as s:
            async with s.begin():
                # 분석 시점 영상 전체 댓글 수를 스냅샷한다 — 이후 videos.comment_count와
                # 비교해 신선도 배너를 띄운다. 통계 미갱신 영상은 수집분으로 폴백.
                current_total = (
                    await s.execute(
                        select(Video.comment_count).where(Video.video_pk == video_pk)
                    )
                ).scalar_one_or_none()
                await s.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(
                        status=STATUS_DONE,
                        fetched_count=len(comments),
                        total_count=current_total or len(comments),
                        positive_count=len(grouped[LABEL_POSITIVE]),
                        negative_count=len(grouped[LABEL_NEGATIVE]),
                        neutral_count=len(grouped[LABEL_NEUTRAL]),
                        result=result,
                        model=model,
                        partial=failures > 0,
                        error=None,
                        analyzed_at=datetime.now(timezone.utc),
                    )
                )
    except Exception as e:  # noqa: BLE001 — 백그라운드 작업은 예외를 삼키고 상태로 남긴다
        print(f"[comment-analysis] 실패: {e}")
        await _fail(str(e))
