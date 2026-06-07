"""영상 분석 파이프라인 (그룹 컨텍스트).

그룹의 AI agent 설정과 프롬프트를 주입받아 LLM을 호출하고, 결과를
그룹 데이터 평면 세션(schema_translate_map 바인딩)에 저장한다.

경로 A: primary_model로 Gemini native (fileData)
경로 B: fallback_model로 OpenAI 호환 엔드포인트 폴백
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.tag import Tag, VideoTag
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
        self, video_pk: int, video_url: str, channel_name: str, published_at_str: str
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
            )
            _validate(result.data)
            return AnalysisPipelineResult(
                data=result.data,
                route="A",
                model_name=self._ai.primary_model,
                gateway_url=self._ai.base_url,
                raw_text=result.raw_text,
            )
        except (LiteLLMError, AnalysisValidationError) as e:
            print(f"경로 A 실패 (video_pk={video_pk}): {e}")

        # 경로 B: OpenAI 호환 폴백
        # 영상 URL을 프롬프트에 명시해 환각 방지.
        # 경로 B는 영상 콘텐츠에 직접 접근 불가 — URL만 참고 가능하므로
        # confidence_score 0.3 이하 + '⚠️추정' 강제를 모델에게 명시한다.
        _path_b_notice = (
            f"\n\n## ⚠️ 주의 (경로 B 폴백)\n"
            f"영상 URL: {video_url}\n"
            f"이 경로는 영상 콘텐츠(영상·음성)에 직접 접근할 수 없습니다. "
            f"URL과 채널명 외에 영상 내용을 확인할 수 없으므로, "
            f"반드시 confidence_score를 0.3 이하로 설정하고 "
            f"모든 본문 텍스트에 '⚠️추정'을 명시해야 합니다."
        )
        try:
            chat = await self._llm.chat(
                model=self._ai.fallback_model,
                messages=[{"role": "user", "content": prompt + _path_b_notice}],
                temperature=self._ai.temperature,
                max_tokens=self._ai.max_tokens,
            )
            data = json.loads(_strip_code_fence(chat.content))
            _validate(data)
            return AnalysisPipelineResult(
                data=data,
                route="B",
                model_name=self._ai.fallback_model,
                gateway_url=self._ai.base_url,
                raw_text=chat.content,
            )
        except Exception as e:
            raise AnalysisFailedError(f"경로 A·B 모두 실패 (video_pk={video_pk}): {e}") from e

    async def save_to_db(
        self, session: AsyncSession, video_pk: int, result: AnalysisPipelineResult
    ) -> None:
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

        await self._save_tags(session, video_pk, data.get("tags") or [])

        await session.execute(
            update(Video).where(Video.video_pk == video_pk).values(analysis_status="done")
        )

        if self._notify_callback:
            await self._notify_callback(video_pk)

    async def _save_tags(
        self, session: AsyncSession, video_pk: int, raw_tags: List[Dict[str, Any]]
    ) -> None:
        involved: list[int] = []
        for t in raw_tags:
            name = (t.get("name") or "").strip()
            if not name:
                continue
            tag_type = t.get("type") or "topic"
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

    async def run_and_save(
        self, session: AsyncSession, video_pk: int, video_url: str, channel_name: str, published_at_str: str
    ) -> AnalysisPipelineResult:
        await session.execute(
            update(Video).where(Video.video_pk == video_pk).values(analysis_status="processing")
        )
        await session.flush()
        try:
            result = await self.run(video_pk, video_url, channel_name, published_at_str)
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
) -> AnalysisPipeline:
    """그룹의 AI agent 설정 + 프롬프트로 파이프라인 생성."""
    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group_id)
    prompts = await mgr.get_prompts(group_id)
    llm = LiteLLMClient(settings=ai)
    return AnalysisPipeline(
        llm_client=llm,
        ai_settings=ai,
        analysis_prompt=(
            analysis_prompt_override.strip()
            if analysis_prompt_override and analysis_prompt_override.strip()
            else prompts.analysis_prompt
        ),
        notify_callback=notify_callback,
    )
