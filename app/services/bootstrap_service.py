"""그룹 생성 시 LLM으로 프로필(persona + digest 섹션)을 자동 생성한다.

프롬프트를 쓰지 않는 사용자를 위해 그룹 이름·카테고리·채널로 카테고리에 맞는
digest 구성을 시드한다. 실패 시 중립 기본값으로 조용히 폴백한다(현행 대비 무열화).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.control.group import Group
from app.services.ai_usage_service import budget_ok_for_group, record_usage
from app.services.digest_sections import DEFAULT_DIGEST_SECTIONS, normalize_sections
from app.services.global_settings import resolve_ai_gateway
from app.services.llm_client import LiteLLMClient
from app.services.settings_manager import get_settings_manager

_BOOTSTRAP_PROMPT = """너는 유튜브 모니터링 그룹의 요약 리포트를 설계하는 어시스턴트다.
아래 그룹에 맞는 (1) 리포트 작성자 페르소나 한 문장과 (2) 주간 리포트 섹션 4~6개를 제안하라.

## 그룹 정보
- 이름: {name}
- 설명: {description}
- 등록 채널(일부): {channels}

## 섹션 규칙
- kind는 'llm'(LLM이 서술) 또는 'computed'(집계 자동) 중 하나.
- computed는 다음 key만 허용: top_tags, top_channels, top_viewed, sentiment_breakdown, stats_overview.
- llm 섹션의 key는 영문 스네이크케이스, guide는 한 줄 작성 지침.
- 이 그룹 주제에 맞게. 투자 전용 표현('종목') 강요 금지.

## 출력 형식 (JSON만)
{{
  "persona": "<이 리포트를 쓰는 애널리스트를 한 문장으로>",
  "digest_sections": [
    {{"key": "overview", "kind": "llm", "title": "핵심 요약", "guide": "..."}},
    {{"key": "top_viewed", "kind": "computed", "title": "조회수 상위"}}
  ]
}}"""


def normalize_bootstrap_output(raw: str) -> tuple[str, list[dict]]:
    """LLM 응답 → (persona, sections). 섹션 2개 미만이면 중립 기본값. 불량 JSON은 ValueError."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bootstrap 응답이 객체가 아님")
    persona = str(data.get("persona") or "").strip()
    sections = normalize_sections(data.get("digest_sections"))
    if len(sections) < 2:
        sections = DEFAULT_DIGEST_SECTIONS
    return persona, sections


async def _channel_names(group: Group, limit: int = 20) -> list[str]:
    """그룹 데이터 평면에서 등록 채널명 최대 limit개. 실패 시 빈 목록."""
    from sqlalchemy import select
    from app.models.pg.channel import Channel
    from app.services.db_engine import data_plane_engine_manager as dpm

    try:
        async with dpm.group_session(group) as session:
            rows = await session.execute(select(Channel.channel_name).limit(limit))
            return [r[0] for r in rows.all() if r[0]]
    except Exception:
        return []


async def bootstrap_profile(group: Group, *, force: bool = False) -> None:
    """프로필을 생성해 app.settings category='profile'에 저장. 실패는 status만 기록."""
    mgr = get_settings_manager()
    if not force:
        existing = await mgr.get_profile(group.group_id)
        if existing.bootstrap_status == "done":
            return

    ok, _reason = await budget_ok_for_group(group)
    if not ok:
        await _save_status(group.group_id, "failed")
        return

    channels = await _channel_names(group)
    prompt = _BOOTSTRAP_PROMPT.format(
        name=group.name or group.slug,
        description=group.description or "(설명 없음)",
        channels=", ".join(channels) if channels else "(아직 없음)",
    )
    ai = await resolve_ai_gateway(group.group_id)
    model = ai.digest_model or ai.primary_model
    client = LiteLLMClient(ai)
    try:
        chat = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=min(ai.max_tokens or 2048, 2048),
            response_format={"type": "json_object"},
        )
        await record_usage(
            user_id=group.owner_user_id, group_id=group.group_id,
            purpose="bootstrap", model=model,
            input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
        )
        persona, sections = normalize_bootstrap_output(chat.content)
        await mgr.set_values(group.group_id, "profile", [
            {"key": "persona", "value": persona, "value_type": "string"},
            {"key": "digest_sections", "value": json.dumps(sections, ensure_ascii=False),
             "value_type": "json"},
            {"key": "bootstrap_status", "value": "done", "value_type": "string"},
            {"key": "bootstrap_at", "value": datetime.now(timezone.utc).isoformat(),
             "value_type": "string"},
        ])
    except Exception as e:  # noqa: BLE001 — 부트스트랩 실패는 그룹 동작을 막지 않는다
        print(f"[bootstrap] {group.slug} 실패: {e}")
        await _save_status(group.group_id, "failed")
    finally:
        await client.aclose()


async def _save_status(group_id: int, status: str) -> None:
    await get_settings_manager().set_values(group_id, "profile", [
        {"key": "bootstrap_status", "value": status, "value_type": "string"},
    ])
