"""Digest 섹션 레지스트리·정규화·조립 (순수 함수).

섹션 형식: {"key": str, "kind": "llm"|"computed", "title": str, "guide": str}
- llm:      LLM이 body_md(markdown)를 생성.
- computed: 집계에서 data(dict)를 생성 (LLM 불필요).
설정(config)과 산출물(digest_sections)이 같은 key/kind/title을 공유한다.
"""

from __future__ import annotations

import json
from typing import Any

SECTION_KIND_LLM = "llm"
SECTION_KIND_COMPUTED = "computed"
SECTION_KIND_HYBRID = "hybrid"

# 피벗 섹션 레지스트리(Phase 3): data는 agg.records_data에서 온다(레코드 기반).
PIVOT_SECTIONS: dict[str, str] = {
    "entity_pivot": "엔티티 집중 분석",
    "period_compare": "지난 기간 대비",
    "top_records": "수치 상위",
}

MAX_SECTIONS = 12
_MAX_GUIDE_LEN = 300

# computed 섹션 레지스트리: key -> 기본 제목. 데이터는 build_computed_data가 만든다.
COMPUTED_SECTIONS: dict[str, str] = {
    "stats_overview": "이번 기간 개요",
    "sentiment_breakdown": "평가 분포",
    "top_tags": "주요 태그",
    "top_channels": "주요 채널",
    "top_viewed": "조회수 상위",
}

# 카테고리 중립 기본 세트(부트스트랩 실패·프로필 부재 시 폴백).
DEFAULT_DIGEST_SECTIONS: list[dict[str, Any]] = [
    {"key": "overview", "kind": SECTION_KIND_LLM, "title": "핵심 요약",
     "guide": "이번 기간을 가로지르는 3~5개 핵심 흐름을 개조식으로 서술"},
    {"key": "perspectives", "kind": SECTION_KIND_LLM, "title": "관점 비교",
     "guide": "합의된 관점과 엇갈리는 관점을 구분해 대비"},
    {"key": "insights", "kind": SECTION_KIND_LLM, "title": "핵심 인사이트",
     "guide": "시청자가 실제 판단에 쓸 수 있는 구체적 인사이트"},
    {"key": "top_viewed", "kind": SECTION_KIND_COMPUTED, "title": "조회수 상위"},
    {"key": "top_tags", "kind": SECTION_KIND_COMPUTED, "title": "주요 태그"},
]


def _clean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _clean_pivot_params(raw: Any) -> dict:
    """피벗 params 정규화: record_type/group_by(str), top_k(1~20 int)만 통과."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("record_type", "group_by"):
        v = _clean(raw.get(k))
        if v:
            out[k] = v
    try:
        tk = int(raw.get("top_k"))
        if 1 <= tk <= 20:
            out["top_k"] = tk
    except (TypeError, ValueError):
        pass
    return out


def normalize_sections(raw: Any) -> list[dict[str, Any]]:
    """외부 입력을 검증된 섹션 배열로. 불량 항목은 drop, 상한 적용."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        kind = _clean(item.get("kind"))
        if not key or kind not in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED, SECTION_KIND_HYBRID):
            continue
        if kind == SECTION_KIND_COMPUTED and key not in COMPUTED_SECTIONS:
            continue
        if kind == SECTION_KIND_HYBRID and key not in PIVOT_SECTIONS:
            continue
        title = _clean(item.get("title"))
        if not title:
            if kind == SECTION_KIND_COMPUTED:
                title = COMPUTED_SECTIONS.get(key, key)
            elif kind == SECTION_KIND_HYBRID:
                title = PIVOT_SECTIONS.get(key, key)
            else:
                title = key
        section: dict[str, Any] = {"key": key, "kind": kind, "title": title}
        guide = _clean(item.get("guide"))[:_MAX_GUIDE_LEN]
        if kind in (SECTION_KIND_LLM, SECTION_KIND_HYBRID) and guide:
            section["guide"] = guide
        if kind == SECTION_KIND_HYBRID:
            params = _clean_pivot_params(item.get("params"))
            if params:
                section["params"] = params
        out.append(section)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def resolve_sections(
    cfg_sections: Any, profile_sections: Any
) -> list[dict[str, Any]]:
    """설정 섹션 우선, 없으면 프로필 섹션, 그것도 없으면 중립 기본값."""
    cfg = normalize_sections(cfg_sections)
    if cfg:
        return cfg
    prof = normalize_sections(profile_sections)
    if prof:
        return prof
    return DEFAULT_DIGEST_SECTIONS


def _fmt_views(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    if n >= 1000:
        return f"{n / 1000:.1f}천"
    return str(n)


def build_computed_data(key: str, agg: Any) -> dict[str, Any]:
    """computed 섹션의 표시용 data(dict). 미등록 key는 빈 dict."""
    if key in PIVOT_SECTIONS:
        return dict((getattr(agg, "records_data", {}) or {}).get(key) or {})
    if key == "stats_overview":
        return {"video_count": getattr(agg, "video_count", 0)}
    if key == "sentiment_breakdown":
        return {"breakdown": dict(getattr(agg, "sentiment_breakdown", {}) or {})}
    if key == "top_tags":
        return {"items": list(getattr(agg, "top_tags", []) or [])[:20]}
    if key == "top_channels":
        return {"items": list(getattr(agg, "top_channels", []) or [])[:10]}
    if key == "top_viewed":
        vids = [v for v in getattr(agg, "videos", []) or [] if getattr(v, "view_count", 0)]
        vids.sort(key=lambda v: v.view_count or 0, reverse=True)
        items = []
        for v in vids[:6]:
            head = (getattr(v, "headline", None) or getattr(v, "one_line", None)
                    or getattr(v, "title", None) or "").strip()
            items.append({"channel": getattr(v, "channel_name", ""), "head": head,
                          "views": v.view_count})
        return {"items": items}
    return {}


def _computed_to_markdown(section: dict[str, Any]) -> str:
    key = section.get("key")
    data = section.get("data") or {}
    lines: list[str] = []
    if key == "stats_overview":
        lines.append(f"- 분석 영상 {data.get('video_count', 0)}건")
    elif key == "sentiment_breakdown":
        for k, v in (data.get("breakdown") or {}).items():
            lines.append(f"- {k}: {v}")
    elif key in ("top_tags", "top_channels"):
        for it in data.get("items") or []:
            lines.append(f"- {it.get('name')} ({it.get('count')})")
    elif key == "top_viewed":
        for it in data.get("items") or []:
            views = _fmt_views(it.get("views"))
            suffix = f" · 조회 {views}" if views else ""
            lines.append(f"- [{it.get('channel')}] {it.get('head')}{suffix}")
    elif key == "entity_pivot":
        for it in data.get("items") or []:
            samples = " / ".join(it.get("samples") or [])
            by = it.get("by") or {}
            suffix = f" — {samples}" if samples else ""
            if by:
                suffix += " (" + ", ".join(f"{k} {v}" for k, v in by.items()) + ")"
            lines.append(f"- **{it.get('entity')}** {it.get('count')}건{suffix}")
    elif key == "period_compare":
        for label, arr_key in (("신규", "new"), ("소멸", "gone")):
            arr = data.get(arr_key) or []
            if arr:
                lines.append(f"- {label}: " + ", ".join(x.get("entity", "") for x in arr))
        for x in data.get("continuing") or []:
            lines.append(f"- 지속: {x.get('entity')} ({x.get('prev')}→{x.get('cur')}건)")
    elif key == "top_records":
        for it in data.get("items") or []:
            head = it.get("entity") or it.get("text") or ""
            date_txt = f" · {it.get('date')}" if it.get("date") else ""
            lines.append(f"- {head}: {it.get('value')}{date_txt}")
    return "\n".join(lines)


def sections_to_markdown(sections: list[dict[str, Any]]) -> str:
    """산출 섹션 배열(body_md/data 포함) → 단일 마크다운. summary_md·공유페이지·폴백용."""
    blocks: list[str] = []
    for s in sections:
        title = _clean(s.get("title"))
        header = f"## {title}" if title else ""
        if s.get("kind") == SECTION_KIND_LLM:
            body = _clean(s.get("body_md"))
        elif s.get("kind") == SECTION_KIND_HYBRID:
            parts = [p for p in (_clean(s.get("body_md")), _computed_to_markdown(s)) if p]
            body = "\n\n".join(parts)
        else:
            body = _computed_to_markdown(s)
        if not body:
            continue
        blocks.append(f"{header}\n{body}".strip())
    return "\n\n".join(blocks)


def build_structured_prompt(
    *, persona: str, data_block: str, sections: list[dict[str, Any]],
    records_data: dict | None = None,
) -> str:
    """페르소나(1층) + 데이터 블록 + llm/hybrid 섹션 출력 스키마(2층)로 프롬프트 조립."""
    persona = persona.strip() or "너는 유튜브 콘텐츠를 종합하는 애널리스트다."
    llm_sections = [
        s for s in sections
        if s.get("kind") in (SECTION_KIND_LLM, SECTION_KIND_HYBRID)
    ]
    schema_lines = []
    for s in llm_sections:
        guide = _clean(s.get("guide")) or s.get("title") or s.get("key")
        schema_lines.append(f'    {{"key": "{s["key"]}", "body_md": "<{guide}>"}}')
    sections_schema = ",\n".join(schema_lines)
    # 피벗 블록은 서술할 hybrid 섹션이 있을 때만 주입한다(없으면 토큰 낭비).
    has_hybrid = any(s.get("kind") == SECTION_KIND_HYBRID for s in sections)
    records_block = ""
    if records_data and has_hybrid:
        records_block = (
            "\n\n## 레코드 집계(피벗) — 해당 섹션은 아래 수치를 근거로 서술하라\n"
            + json.dumps(records_data, ensure_ascii=False)
        )
    return f"""{persona}

아래 자료를 바탕으로 이번 기간을 한국어 개조식('~함','~임')으로 종합하라.
개별 영상 나열이 아니라 여러 영상에 걸친 흐름을 묶어 서술할 것.

## 자료
{data_block}{records_block}

## 출력 형식
반드시 아래 JSON으로만 출력. sections 배열은 지정된 key를 순서대로 포함:
{{
  "headline": "<이모지 1~2개 포함, 이번 기간 핵심 한 줄(40자 이내)>",
  "sections": [
{sections_schema}
  ],
  "telegram_summary": "<텔레그램용 400자 이내 일반 텍스트 브리핑>"
}}"""


def parse_structured_response(
    raw: str, *, requested_keys: list[str]
) -> tuple[str, dict[str, str], str]:
    """LLM JSON 응답 → (headline, {key: body_md}, telegram_summary). 실패 시 빈 값."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "", {}, ""
    if not isinstance(data, dict):
        return "", {}, ""
    wanted = set(requested_keys)
    bodies: dict[str, str] = {}
    for item in data.get("sections") or []:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        body = _clean(item.get("body_md"))
        if key in wanted and body:
            bodies[key] = body
    return _clean(data.get("headline")), bodies, _clean(data.get("telegram_summary"))


def assemble_output_sections(
    sections: list[dict[str, Any]], *, llm_bodies: dict[str, str], agg: Any
) -> list[dict[str, Any]]:
    """설정 섹션 순서대로 산출 섹션 배열 생성. llm은 body_md, computed는 data."""
    out: list[dict[str, Any]] = []
    for s in sections:
        base = {"key": s["key"], "kind": s["kind"], "title": s.get("title", "")}
        if s["kind"] == SECTION_KIND_LLM:
            body = llm_bodies.get(s["key"], "")
            if not body:
                continue
            out.append({**base, "body_md": body})
        elif s["kind"] == SECTION_KIND_HYBRID:
            data = build_computed_data(s["key"], agg)
            body = llm_bodies.get(s["key"], "")
            if not body and not any(bool(v) for v in data.values()):
                continue  # 데이터·서술 모두 빈 하이브리드 섹션은 생략
            sec = {**base, "data": data}
            if body:
                sec["body_md"] = body
            out.append(sec)
        else:
            out.append({**base, "data": build_computed_data(s["key"], agg)})
    return out
