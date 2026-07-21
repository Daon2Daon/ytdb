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


def _to_num(v: Any):
    try:
        s = str(v).strip().replace(",", "")
        if s == "":
            return None
        f = float(s)
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return None


def _to_date(v: Any):
    from datetime import datetime as _dt
    s = str(v).strip()
    try:
        return _dt.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def promote_fields(type_def: dict, values: dict) -> dict | None:
    """type_def(정규화됨) + LLM이 준 필드 dict → AnalysisRecord 컬럼 dict.

    승격: 첫 entity→entity_name, 첫 text→value_text, 첫 number→value_num,
    첫 date→event_date. 나머지·스키마 밖 필드·파싱실패 원문 → attrs.
    required 필드가 비면 None(drop).
    """
    fields = type_def.get("fields") or []
    by_key = {f["key"]: f for f in fields}
    picked = {"entity": None, "text": None, "number": None, "date": None}
    row = {"entity_name": None, "value_text": None, "value_num": None, "event_date": None}
    attrs: dict[str, Any] = {}

    for f in fields:
        key, dt = f["key"], f["datatype"]
        if key not in values:
            continue
        raw = values[key]
        raw_str = "" if raw is None else str(raw).strip()
        if dt == "entity" and picked["entity"] is None and raw_str:
            picked["entity"] = key
            row["entity_name"] = raw_str
        elif dt == "text" and picked["text"] is None and raw_str:
            picked["text"] = key
            row["value_text"] = raw_str
        elif dt == "number" and picked["number"] is None:
            picked["number"] = key
            num = _to_num(raw)
            if num is None:
                attrs[key] = raw_str
            else:
                row["value_num"] = num
        elif dt == "date" and picked["date"] is None:
            picked["date"] = key
            d = _to_date(raw)
            if d is None:
                attrs[key] = raw_str
            else:
                row["event_date"] = d
        else:
            if raw_str:
                attrs[key] = raw_str

    for key, raw in values.items():
        if key in by_key:
            continue
        raw_str = "" if raw is None else str(raw).strip()
        if raw_str:
            attrs[key] = raw_str

    for f in fields:
        if f.get("required"):
            has = (
                (f["datatype"] == "entity" and row["entity_name"])
                or (f["datatype"] == "text" and row["value_text"])
                or (f["datatype"] == "number" and row["value_num"] is not None)
                or (f["datatype"] == "date" and row["event_date"] is not None)
                or (f["key"] in attrs)
            )
            if not has:
                return None

    row["attrs"] = attrs
    return row
