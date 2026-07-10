"""영상 분석 파이프라인 (그룹 컨텍스트).

그룹의 AI agent 설정과 프롬프트를 주입받아 LLM을 호출하고, 결과를
그룹 데이터 평면 세션(schema_translate_map 바인딩)에 저장한다.

primary_model로 Gemini native passthrough (fileData) 호출.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.tag import ALLOWED_TAG_TYPES, DEFAULT_TAG_TYPE, Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.llm_client import LiteLLMClient, LiteLLMError
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import AIGatewaySettings
from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY

PROMPT_VERSION = "v4.0"

DEFAULT_ANALYSIS_PROMPT: str = """다음 유튜브 영상을 한국어로 분석해줘.

## 현재 날짜
오늘은 {today}입니다. 업로드 일시가 {published_at_kst}인 이 영상은 현재 시점에서 이미 게시된 영상이므로 반드시 실제 내용을 기반으로 분석할 것.

## 영상 정보
- 채널명: {channel_name}
- 업로드 일시: {published_at_kst}

## 작성 원칙 (반드시 준수)
영상이 '무엇을 했는가'(행위 서술)가 아닌, '무엇을 주장·예측·결론 내리는가'(내용 서술)를 중심으로 작성.
금지 표현: ~을 제시했다 / ~을 논했다 / ~을 설명했다 / ~을 다뤘다 / ~을 예측했다

## 분석 요청 항목
- 한 줄 요약, 헤드라인(이모지+키워드 40자 이내), 짧은 요약(800자 이내), 주요 내용(5~10개),
  전체 분석(analysis_sections): 섹션 배열로 작성. 각 섹션은 {key, title, bullets[]} 구조.
  key는 영문 스네이크케이스(예: overview, main_points, conclusion).
  title은 한국어 섹션 제목. bullets는 한 문장씩 담은 문자열 배열.
  bullets 항목에는 기호(•, -, 번호)와 줄바꿈(\n)을 넣지 말 것. 강조는 **굵게**만 허용.
  타임스탬프 포인트, 인사이트(3~5개), 등장 인물/기업/지표,
  감성(bullish/bearish/neutral/mixed), 태그(5~10개, 한국어 정규화), 신뢰도(0.0~1.0).

## 출력 형식
반드시 아래 JSON 형식으로만 출력. 모든 텍스트는 한국어 개조식('~함','~임').

{
  "one_line": "string",
  "headline": "string",
  "short_summary_md": "string",
  "bullet_points": ["string"],
  "analysis_sections": [{"key": "string", "title": "string", "bullets": ["string"]}],
  "key_points": [{"timestamp":"hh:mm:ss","point":"string"}],
  "insights": ["string"],
  "entities": [{"type":"person|company|ticker|metric","name":"string"}],
  "sentiment": "bullish|bearish|neutral|mixed",
  "tags": [{"name":"string","type":"topic|ticker|person|sector","weight":0.0}],
  "confidence_score": 0.0
}"""

# UI가 실제로 필요로 하는 핵심 필드만 필수로 강제한다.
# sentiment/confidence_score/headline/bullet_points 등은 선택값으로,
# 그룹 성격(경제·마케팅·통신 등)에 따라 없거나 자유 값이어도 분석을 실패시키지 않는다.
# sentiment는 자유 문자열로 저장한다(경제="bullish", 마케팅="trendy" 등). 경제 enum 강제 안 함.
REQUIRED_FIELDS = {
    "one_line",
    "short_summary_md",
}


# Gemini fileData는 기본 1fps로 영상을 샘플링한다. 긴 영상은 프레임 토큰이
# 입력 한도(약 100만 토큰)를 넘어 INVALID_ARGUMENT(400)로 거부된다.
# 샘플 프레임 수가 이 상한을 넘지 않도록 fps를 길이에 맞춰 낮춘다.
_MAX_VIDEO_FRAMES = 1500


def _fps_for_duration(duration_seconds: Optional[int]) -> Optional[float]:
    """영상 길이에 맞춘 샘플링 fps. 짧으면 None(기본 1fps), 길면 낮춘 값."""
    if not duration_seconds or duration_seconds <= _MAX_VIDEO_FRAMES:
        return None
    return round(_MAX_VIDEO_FRAMES / duration_seconds, 3)


def _coerce_confidence(v: Any) -> Optional[float]:
    """신뢰도 값을 관대하게 변환한다. 0~1 범위의 숫자만 채택, 그 외/잘못된 값은 None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


class AnalysisFailedError(RuntimeError):
    """경로 A·B 모두 실패."""


class AnalysisValidationError(ValueError):
    """응답 검증 실패."""


@dataclass
class AnalysisPipelineResult:
    data: Dict[str, Any]
    route: str
    model_name: str
    gateway_url: str
    prompt_version: str = PROMPT_VERSION
    raw_text: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


def _validate(data: Dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise AnalysisValidationError(f"필수 필드 누락: {missing}")
    # sentiment/confidence_score는 선택값 — 검증으로 막지 않는다(저장 단계에서 관대 처리).


def _published_at_kst(published_at_str: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return published_at_str


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(line for line in t.splitlines() if not line.startswith("```")).strip()
    return t


def result_from_cache(
    data: Dict[str, Any], model_name: str, gateway_url: str = ""
) -> AnalysisPipelineResult:
    """공유 캐시(app.analysis_cache)의 analysis JSON → 파이프라인 결과 객체."""
    return AnalysisPipelineResult(
        data=data, route="cache", model_name=model_name, gateway_url=gateway_url
    )


async def save_tags_for_video(
    session: AsyncSession, video_pk: int, raw_tags: List[Dict[str, Any]]
) -> None:
    involved: list[int] = []
    for t in raw_tags:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        # LLM이 허용 외 type(예: company, event)을 반환하면 CHECK 위반으로
        # 트랜잭션이 깨지므로 화이트리스트로 정규화하고 미허용 값은 기본값 폴백.
        tag_type = (t.get("type") or DEFAULT_TAG_TYPE).strip().lower()
        if tag_type not in ALLOWED_TAG_TYPES:
            tag_type = DEFAULT_TAG_TYPE
        weight = t.get("weight")
        ins = (
            pg_insert(Tag)
            .values(name=name, tag_type=tag_type)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(Tag.tag_pk)
        )
        tag_pk = (await session.execute(ins)).scalar()
        if tag_pk is None:
            tag_pk = (
                await session.execute(select(Tag.tag_pk).where(Tag.name == name))
            ).scalar()
        if tag_pk is None:
            continue
        await session.execute(
            pg_insert(VideoTag)
            .values(video_pk=video_pk, tag_pk=tag_pk, weight=weight)
            .on_conflict_do_nothing(index_elements=["video_pk", "tag_pk"])
        )
        involved.append(tag_pk)

    # 연관된 태그의 video_count 재계산
    for tag_pk in involved:
        cnt = (
            await session.execute(
                select(func.count()).select_from(VideoTag).where(VideoTag.tag_pk == tag_pk)
            )
        ).scalar()
        await session.execute(
            update(Tag).where(Tag.tag_pk == tag_pk).values(video_count=cnt)
        )


async def save_analysis_to_group(
    session: AsyncSession,
    video_pk: int,
    result: AnalysisPipelineResult,
    notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
) -> None:
    """분석 결과를 그룹 스키마에 저장 (video_analysis upsert + 태그 + 상태 done).

    LLM 호출 여부와 무관한 순수 저장 경로 — 캐시 적중 복사와 신규 분석이 공용.
    """
    data = result.data
    await session.execute(
        update(Video)
        .where(Video.video_pk == video_pk, Video.share_token.is_(None))
        .values(share_token=generate_share_token(), share_visibility=DEFAULT_VISIBILITY)
    )
    stmt = pg_insert(VideoAnalysis).values(
        video_pk=video_pk,
        one_line=data.get("one_line", ""),
        headline=data.get("headline"),
        short_summary_md=data.get("short_summary_md", ""),
        bullet_points=data.get("bullet_points"),
        full_analysis_md=data.get("full_analysis_md"),
        analysis_sections=data.get("analysis_sections"),
        key_points=data.get("key_points"),
        insights=data.get("insights"),
        entities=data.get("entities"),
        sentiment=data.get("sentiment"),
        confidence_score=_coerce_confidence(data.get("confidence_score")),
        model_name=result.model_name,
        gateway_url=result.gateway_url,
        prompt_version=result.prompt_version,
        analyzed_at=datetime.now(timezone.utc),
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["video_pk"],
        set_={
            c: stmt.excluded[c]
            for c in (
                "one_line",
                "headline",
                "short_summary_md",
                "bullet_points",
                "full_analysis_md",
                "analysis_sections",
                "key_points",
                "insights",
                "entities",
                "sentiment",
                "confidence_score",
                "model_name",
                "gateway_url",
                "prompt_version",
                "analyzed_at",
            )
        },
    )
    await session.execute(upsert)

    await save_tags_for_video(session, video_pk, data.get("tags") or [])

    await session.execute(
        update(Video).where(Video.video_pk == video_pk).values(analysis_status="done")
    )

    if notify_callback:
        await notify_callback(video_pk)


class AnalysisPipeline:
    def __init__(
        self,
        llm_client: LiteLLMClient,
        ai_settings: AIGatewaySettings,
        analysis_prompt: str,
        notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
    ) -> None:
        self._llm = llm_client
        self._ai = ai_settings
        self._prompt_template = analysis_prompt or DEFAULT_ANALYSIS_PROMPT
        self._notify_callback = notify_callback

    async def aclose(self) -> None:
        """내부 LLM 클라이언트의 HTTP 리소스를 정리한다."""
        await self._llm.aclose()

    def _render(self, channel_name: str, published_at_str: str) -> str:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y년 %m월 %d일")
        # str.format() 대신 알려진 플레이스홀더만 치환한다.
        # (프롬프트의 JSON 예시 내 중괄호가 format 치환 필드로 오인되는 것을 방지)
        return (
            self._prompt_template
            .replace("{today}", today)
            .replace("{channel_name}", channel_name)
            .replace("{published_at_kst}", _published_at_kst(published_at_str))
        )

    async def run(
        self,
        video_pk: int,
        video_url: str,
        channel_name: str,
        published_at_str: str,
        duration_seconds: Optional[int] = None,
    ) -> AnalysisPipelineResult:
        prompt = self._render(channel_name, published_at_str)

        # 경로 A: Gemini native
        try:
            result = await self._llm.analyze_video_native(
                model=self._ai.primary_model,
                video_url=video_url,
                prompt=prompt,
                temperature=self._ai.temperature,
                max_output_tokens=self._ai.max_tokens,
                fps=_fps_for_duration(duration_seconds),
            )
            _validate(result.data)
            return AnalysisPipelineResult(
                data=result.data,
                route="A",
                model_name=self._ai.primary_model,
                gateway_url=self._ai.base_url,
                raw_text=result.raw_text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        except (LiteLLMError, AnalysisValidationError) as e:
            raise AnalysisFailedError(
                f"경로 A 실패 (video_pk={video_pk}, url={video_url}, model={self._ai.primary_model}): {e}"
            ) from e

    async def save_to_db(
        self, session: AsyncSession, video_pk: int, result: AnalysisPipelineResult
    ) -> None:
        await save_analysis_to_group(
            session, video_pk, result, notify_callback=self._notify_callback
        )

    async def run_and_save(
        self,
        session: AsyncSession,
        video_pk: int,
        video_url: str,
        channel_name: str,
        published_at_str: str,
        duration_seconds: Optional[int] = None,
    ) -> AnalysisPipelineResult:
        await session.execute(
            update(Video).where(Video.video_pk == video_pk).values(analysis_status="processing")
        )
        await session.flush()
        try:
            result = await self.run(
                video_pk, video_url, channel_name, published_at_str, duration_seconds
            )
            await self.save_to_db(session, video_pk, result)
            return result
        except Exception as e:
            await session.execute(
                update(Video)
                .where(Video.video_pk == video_pk)
                .values(analysis_status="failed", analysis_error=str(e)[:500])
            )
            raise


async def build_analysis_pipeline(
    group_id: int,
    notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
    analysis_prompt_override: Optional[str] = None,
    resolved: Optional["ResolvedPrompts"] = None,
) -> AnalysisPipeline:
    """그룹의 AI agent 설정 + 해석된 프롬프트로 파이프라인 생성.

    프롬프트는 preset_service.resolve_prompts()를 경유한다(프리셋 우선, 직접 폴백).
    호출 측이 이미 해석했다면 resolved로 넘겨 중복 조회를 피한다.
    """
    from app.services.preset_service import ResolvedPrompts, resolve_prompts

    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group_id)
    if resolved is None:
        resolved = await resolve_prompts(group_id)
    llm = LiteLLMClient(settings=ai)
    return AnalysisPipeline(
        llm_client=llm,
        ai_settings=ai,
        analysis_prompt=(
            analysis_prompt_override.strip()
            if analysis_prompt_override and analysis_prompt_override.strip()
            else resolved.analysis_prompt
        ),
        notify_callback=notify_callback,
    )
