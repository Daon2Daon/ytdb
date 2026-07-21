"""분석 산출물 텍스트에서 구조화 레코드를 추출하는 2차 경량 LLM 패스.

분석 본 호출을 건드리지 않는다(공유 캐시 보존). save_analysis_to_group 완료 후
best-effort로 실행 — 실패·지연이 분석을 깨뜨리지 않는다.
record_schema 없는 그룹은 전체 skip(무비용).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.models.control.group import Group
from app.models.pg.analysis_record import AnalysisRecord
from app.models.pg.entity import Entity
from app.models.pg.video_analysis import VideoAnalysis
from app.services.ai_usage_service import budget_ok_for_group, record_usage
from app.services.entity_service import resolve_and_register
from app.services.global_settings import resolve_ai_gateway
from app.services.llm_client import LiteLLMClient
from app.services.records_schema import map_vocab_value, promote_fields
from app.services.settings_manager import get_settings_manager

_RECORDS_PROMPT = """너는 분석 결과에서 구조화된 사실을 뽑아내는 추출기다.
아래 분석 본문에서 record_schema에 정의된 유형의 사실만 추출하라.
본문에 근거 없는 내용은 만들지 마라. 없으면 records를 빈 배열로.

## record_schema
{schema}

## 알려진 엔티티(표기 통일용, 같은 대상이면 이 표기를 써라)
{entities}

## 통제 어휘(해당 축의 값은 아래 표기로 정규화)
{vocab}

## 분석 본문
{analysis_text}

## 출력(JSON만)
{{"records": [{{"type": "<type_key>", "fields": {{"<field_key>": "<값>"}}}}]}}"""


def build_records_prompt(
    analysis_text: str, record_schema: dict, top_entities: list[str], vocab: dict
) -> str:
    return _RECORDS_PROMPT.format(
        schema=json.dumps(record_schema, ensure_ascii=False),
        entities=", ".join(top_entities) if top_entities else "(없음)",
        vocab=json.dumps(vocab, ensure_ascii=False) if vocab else "(없음)",
        analysis_text=analysis_text[:8000],
    )


def parse_records_response(raw: str, record_schema: dict) -> list[dict]:
    """LLM 응답 → AnalysisRecord 컬럼 dict 리스트. 관대 파싱(불량 전부 drop)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    records = data.get("records")
    if not isinstance(records, list):
        return []
    types_by_key = {t["type_key"]: t for t in record_schema.get("types") or []}
    out: list[dict] = []
    pos_by_type: dict[str, int] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        tkey = str(item.get("type") or "").strip()
        type_def = types_by_key.get(tkey)
        if type_def is None:
            continue
        fields = item.get("fields")
        if not isinstance(fields, dict):
            continue
        row = promote_fields(type_def, fields)
        if row is None:
            continue
        pos = pos_by_type.get(tkey, 0)
        pos_by_type[tkey] = pos + 1
        row["record_type"] = tkey
        row["position"] = pos
        row["schema_version"] = record_schema.get("version", 1)
        out.append(row)
    return out


def analysis_text_for_extraction(analysis: dict) -> str:
    """저장된 분석 dict에서 추출 입력 텍스트를 만든다(영상 재시청 없음)."""
    parts: list[str] = []
    if analysis.get("one_line"):
        parts.append(str(analysis["one_line"]))
    for sec in analysis.get("analysis_sections") or []:
        if isinstance(sec, dict):
            title = sec.get("title") or sec.get("key") or ""
            bullets = sec.get("bullets") or []
            body = " / ".join(str(b) for b in bullets) if isinstance(bullets, list) else ""
            parts.append(f"{title}: {body}")
    for k in ("insights", "key_points", "entities"):
        v = analysis.get(k)
        if isinstance(v, list):
            parts.append(f"{k}: " + ", ".join(str(x) for x in v))
    return "\n".join(p for p in parts if p.strip())


async def _load_profile(group_id: int):
    return await get_settings_manager().get_profile(group_id)


async def _top_entities(session, limit: int = 30) -> list[str]:
    rows = (await session.execute(
        select(Entity.canonical_name).order_by(Entity.mention_count.desc()).limit(limit)
    )).all()
    return [r[0] for r in rows if r[0]]


async def run_records_extraction(*, group: Group, video_pk: int, analysis: dict) -> None:
    """best-effort. 예외는 전부 삼킨다(분석 성공을 지연·실패시키지 않음)."""
    try:
        profile = await _load_profile(group.group_id)
        record_schema = profile.record_schema
        if not record_schema.get("types"):
            return  # 무비용 skip

        # 추출 입력이 비면 budget DB 조회 전에 먼저 빠져나간다(무비용).
        text_in = analysis_text_for_extraction(analysis)
        if not text_in.strip():
            return

        ok, _ = await budget_ok_for_group(group)
        if not ok:
            return

        from app.services.db_engine import data_plane_engine_manager as dpm
        async with dpm.group_session(group) as session:
            top = await _top_entities(session)

        ai = await resolve_ai_gateway(group.group_id)
        model = ai.tagging_model or ai.primary_model
        prompt = build_records_prompt(text_in, record_schema, top, profile.vocab)
        client = LiteLLMClient(ai)
        try:
            chat = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=min(ai.max_tokens or 2048, 2048),
                response_format={"type": "json_object"},
            )
        finally:
            await client.aclose()

        await record_usage(
            user_id=group.owner_user_id, group_id=group.group_id,
            purpose="records", model=model,
            input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
        )

        rows = parse_records_response(chat.content, record_schema)
        vocab = profile.vocab

        async with dpm.group_session(group) as session:
            async with session.begin():
                pending: list[str] = []
                for row in rows:
                    if row.get("entity_name"):
                        row["entity_name"] = await resolve_and_register(session, row["entity_name"])
                    new_attrs = {}
                    for k, v in (row.get("attrs") or {}).items():
                        if k in vocab:
                            mapped, is_pending = map_vocab_value(v, vocab[k])
                            new_attrs[k] = mapped
                            if is_pending:
                                pending.append(f"{k}:{v}")
                        else:
                            new_attrs[k] = v
                    row["attrs"] = new_attrs

                await session.execute(
                    delete(AnalysisRecord).where(AnalysisRecord.video_pk == video_pk)
                )
                for row in rows:
                    await session.execute(AnalysisRecord.__table__.insert().values(
                        video_pk=video_pk,
                        record_type=row["record_type"],
                        schema_version=row.get("schema_version", 1),
                        position=row["position"],
                        entity_name=row.get("entity_name"),
                        value_text=row.get("value_text"),
                        value_num=row.get("value_num"),
                        event_date=row.get("event_date"),
                        attrs=row.get("attrs") or {},
                        created_at=datetime.now(timezone.utc),
                    ))

                if "sentiment" in vocab and analysis.get("sentiment"):
                    mapped, is_pending = map_vocab_value(analysis["sentiment"], vocab["sentiment"])
                    if mapped != analysis["sentiment"]:
                        await session.execute(
                            VideoAnalysis.__table__.update()
                            .where(VideoAnalysis.video_pk == video_pk)
                            .values(sentiment=mapped)
                        )
                    if is_pending:
                        pending.append(f"sentiment:{analysis['sentiment']}")

        if pending:
            await _append_vocab_pending(group.group_id, pending)
    except Exception as e:  # noqa: BLE001 — 후처리 실패는 분석을 막지 않는다
        print(f"[records] {getattr(group, 'slug', '?')} video_pk={video_pk} 실패: {e}")


async def _append_vocab_pending(group_id: int, new_items: list[str]) -> None:
    """profile.vocab_pending(최근 50개)에 미매핑 값을 적재 — Phase 3 보강 제안 입력."""
    mgr = get_settings_manager()
    d = await mgr.get_typed(group_id, "profile")
    existing = d.get("vocab_pending") or []
    if not isinstance(existing, list):
        existing = []
    merged = (existing + new_items)[-50:]
    await mgr.set_values(group_id, "profile", [
        {"key": "vocab_pending", "value": json.dumps(merged, ensure_ascii=False),
         "value_type": "json"},
    ])
