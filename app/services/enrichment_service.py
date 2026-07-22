# app/services/enrichment_service.py
"""프로필 보강 제안 루프 (Phase 3).

분석 10건 누적 후 월 1회, 최근 표본 + vocab_pending + 병합 보류 후보를 입력으로
부트스트랩 LLM을 재호출해 제안 diff를 만든다. 자동 적용하지 않는다 —
profile.enrich_proposal에 저장하고 사용자가 [적용]/[무시]로 처리한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.digest_sections import normalize_sections
from app.services.records_schema import (
    bump_schema_version_if_changed, normalize_record_schema, normalize_vocab,
)

_DATATYPES = ("entity", "text", "number", "date")

_ENRICH_PROMPT = """너는 유튜브 모니터링 그룹의 리포트 프로필을 점검하는 어시스턴트다.
현재 프로필과 최근 관측을 보고 개선 제안을 JSON diff로만 출력하라.
확실히 유용한 것만 제안하라. 없으면 배열·객체를 비워라.

## 현재 프로필
- persona: {persona}
- digest_sections: {sections}
- record_schema: {record_schema}
- vocab: {vocab}

## 최근 관측
- 최근 분석 한줄 요약: {samples}
- 미매핑 어휘(vocab_pending): {vocab_pending}
- 엔티티 병합 보류 후보: {merge_holds}

## 제안 규칙
- sections_add: 기존에 없는 llm 섹션만, 최대 2개.
- record_fields_add: 기존 type_key에 새 field 추가만, 최대 3개. datatype은 entity|text|number|date.
- vocab_add: 기존 축 values/synonyms 확장 또는 새 축 1개. vocab_pending의 빈번 값을 우선 반영.
- entity_attrs_add: 알려진 엔티티의 속성 보강(예: {{"region": "일본"}}). 확실한 것만.

## 출력(JSON만)
{{"sections_add": [], "record_fields_add": [{{"type_key": "", "field": {{"key": "", "label": "", "datatype": "text", "required": false}}}}], "vocab_add": {{}}, "entity_attrs_add": [{{"entity": "", "attrs": {{}}}}], "note": "<한 줄 요지>"}}"""


def build_enrich_prompt(
    *, persona: str, sections: list, record_schema: dict, vocab: dict,
    samples: list[str], vocab_pending: list[str], merge_holds: list[str],
) -> str:
    return _ENRICH_PROMPT.format(
        persona=persona or "(없음)",
        sections=json.dumps(sections, ensure_ascii=False),
        record_schema=json.dumps(record_schema, ensure_ascii=False),
        vocab=json.dumps(vocab, ensure_ascii=False),
        samples=" / ".join(samples[:20]) or "(없음)",
        vocab_pending=", ".join(vocab_pending[:50]) or "(없음)",
        merge_holds="; ".join(merge_holds[:20]) or "(없음)",
    )


def normalize_enrich_proposal(raw: str) -> dict:
    """LLM 응답 → 검증된 제안 dict. 실질 내용이 없으면 {} (제안 없음)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    sections_add = [
        s for s in normalize_sections(data.get("sections_add"))
        if s.get("kind") == "llm"
    ][:2]
    fields_add: list[dict] = []
    for it in (data.get("record_fields_add") or [])[:3]:
        if not isinstance(it, dict):
            continue
        tkey = str(it.get("type_key") or "").strip()
        f = it.get("field")
        if not tkey or not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()
        if not key:
            continue
        dt = str(f.get("datatype") or "text").strip().lower()
        fields_add.append({"type_key": tkey, "field": {
            "key": key,
            "label": str(f.get("label") or key).strip(),
            "datatype": dt if dt in _DATATYPES else "text",
            "required": bool(f.get("required")),
        }})
    vocab_add = normalize_vocab(data.get("vocab_add"))
    attrs_add: list[dict] = []
    for it in (data.get("entity_attrs_add") or [])[:10]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("entity") or "").strip()
        attrs = it.get("attrs")
        if name and isinstance(attrs, dict) and attrs:
            attrs_add.append({"entity": name,
                              "attrs": {str(k): str(v) for k, v in attrs.items()}})
    if not (sections_add or fields_add or vocab_add or attrs_add):
        return {}
    return {
        "sections_add": sections_add,
        "record_fields_add": fields_add,
        "vocab_add": vocab_add,
        "entity_attrs_add": attrs_add,
        "note": str(data.get("note") or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def proposal_is_empty(p) -> bool:
    if not isinstance(p, dict):
        return True
    return not any(p.get(k) for k in
                   ("sections_add", "record_fields_add", "vocab_add", "entity_attrs_add"))


def should_enrich(*, analysis_count: int, last_at: str, has_proposal: bool,
                  now: datetime) -> bool:
    """분석 10건 도달 + (첫 회 또는 30일 경과) + 미처리 제안 없음."""
    if has_proposal or analysis_count < 10:
        return False
    if not last_at:
        return True
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).days >= 30


def apply_proposal_items(profile_typed: dict, proposal: dict) -> list[dict]:
    """제안 적용 → settings set_values 아이템(순수). 빈 제안이면 []."""
    if proposal_is_empty(proposal):
        return []
    sections = normalize_sections(profile_typed.get("digest_sections"))
    have = {s["key"] for s in sections}
    merged_sections = normalize_sections(
        sections + [s for s in proposal.get("sections_add") or []
                    if s.get("key") not in have])

    old_schema = normalize_record_schema(profile_typed.get("record_schema"))
    new_types = json.loads(json.dumps(old_schema["types"]))  # deep copy
    by_key = {t["type_key"]: t for t in new_types}
    for it in proposal.get("record_fields_add") or []:
        t = by_key.get(it.get("type_key"))
        f = it.get("field") or {}
        if t is None or not f.get("key"):
            continue
        if any(x["key"] == f["key"] for x in t["fields"]):
            continue
        t["fields"].append(f)
    new_schema = bump_schema_version_if_changed(
        old_schema, {"version": old_schema["version"], "types": new_types})

    vocab = normalize_vocab(profile_typed.get("vocab"))
    for axis, spec in (proposal.get("vocab_add") or {}).items():
        cur = vocab.get(axis) or {"label": str(spec.get("label") or axis),
                                  "values": [], "synonyms": {}}
        for v in spec.get("values") or []:
            if v not in cur["values"]:
                cur["values"].append(v)
        cur["synonyms"] = {**cur.get("synonyms", {}), **(spec.get("synonyms") or {})}
        vocab[axis] = cur

    return [
        {"key": "digest_sections",
         "value": json.dumps(merged_sections, ensure_ascii=False), "value_type": "json"},
        {"key": "record_schema",
         "value": json.dumps(new_schema, ensure_ascii=False), "value_type": "json"},
        {"key": "vocab", "value": json.dumps(vocab, ensure_ascii=False),
         "value_type": "json"},
        {"key": "enrich_proposal", "value": "{}", "value_type": "json"},
        {"key": "vocab_pending", "value": "[]", "value_type": "json"},
    ]
