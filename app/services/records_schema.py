"""record_schema·vocab 정규화와 필드→컬럼 승격 (순수 함수)."""

from __future__ import annotations

from datetime import date
from typing import Any

_DATATYPES = ("entity", "text", "number", "date")


def normalize_record_schema(raw: Any) -> dict:
    """LLM/사용자 입력 record_schema를 관대하게 정규화. 항상 유효 구조 반환."""
    if not isinstance(raw, dict):
        return {"version": 1, "types": []}
    version = raw.get("version")
    version = version if isinstance(version, int) and version >= 1 else 1
    types_out: list[dict] = []
    for t in raw.get("types") or []:
        if not isinstance(t, dict):
            continue
        type_key = str(t.get("type_key") or "").strip()
        if not type_key:
            continue
        fields_out: list[dict] = []
        for f in t.get("fields") or []:
            if not isinstance(f, dict):
                continue
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            dt = str(f.get("datatype") or "text").strip().lower()
            if dt not in _DATATYPES:
                dt = "text"
            fields_out.append({
                "key": key,
                "label": str(f.get("label") or key).strip(),
                "datatype": dt,
                "required": bool(f.get("required")),
            })
        if not fields_out:
            continue
        types_out.append({
            "type_key": type_key,
            "label": str(t.get("label") or type_key).strip(),
            "fields": fields_out,
        })
    return {"version": version, "types": types_out}


def normalize_vocab(raw: Any) -> dict:
    """통제 어휘 정규화. {axis: {label, values[], synonyms{}}}."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for axis, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        values = [str(v).strip() for v in (spec.get("values") or []) if str(v).strip()]
        syn_raw = spec.get("synonyms") or {}
        synonyms = {
            str(k).strip(): str(v).strip()
            for k, v in syn_raw.items()
            if str(k).strip() and str(v).strip()
        } if isinstance(syn_raw, dict) else {}
        out[str(axis).strip()] = {
            "label": str(spec.get("label") or axis).strip(),
            "values": values,
            "synonyms": synonyms,
        }
    return out
