"""그룹 프로필 조회·재생성·편집 API (Phase 3: record_schema·vocab·보강 제안 포함)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.services.bootstrap_service import bootstrap_profile
from app.services.digest_sections import normalize_sections
from app.services.records_schema import bump_schema_version_if_changed, normalize_vocab
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/profile", tags=["profile"])


async def _profile_payload(group_id: int) -> dict:
    mgr = get_settings_manager()
    p = await mgr.get_profile(group_id)
    d = await mgr.get_typed(group_id, "profile")
    proposal = d.get("enrich_proposal")
    pending = d.get("vocab_pending")
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
        "bootstrap_at": p.bootstrap_at,
        "record_schema": p.record_schema,
        "vocab": p.vocab,
        "vocab_pending": pending if isinstance(pending, list) else [],
        "enrich_proposal": proposal if isinstance(proposal, dict) else {},
    }


@router.get("")
async def get_profile(group: Group = Depends(get_group_or_404)) -> dict:
    return await _profile_payload(group.group_id)


@router.post("/regenerate")
async def regenerate_profile(group: Group = Depends(get_group_or_404)) -> dict:
    await bootstrap_profile(group, force=True)
    return await _profile_payload(group.group_id)


class ProfileUpdate(BaseModel):
    persona: str | None = None
    digest_sections: list[dict] | None = None
    record_schema: dict | None = None
    vocab: dict | None = None


@router.put("")
async def put_profile(
    body: ProfileUpdate, group: Group = Depends(get_group_or_404)
) -> dict:
    """L2 편집: 제공된 필드만 정규화해 저장. record_schema 변경은 version 증가."""
    mgr = get_settings_manager()
    current = await mgr.get_profile(group.group_id)
    items: list[dict] = []
    if body.persona is not None:
        items.append({"key": "persona", "value": body.persona.strip(),
                      "value_type": "string"})
    if body.digest_sections is not None:
        sections = normalize_sections(body.digest_sections)
        items.append({"key": "digest_sections",
                      "value": json.dumps(sections, ensure_ascii=False),
                      "value_type": "json"})
    if body.record_schema is not None:
        schema = bump_schema_version_if_changed(current.record_schema, body.record_schema)
        items.append({"key": "record_schema",
                      "value": json.dumps(schema, ensure_ascii=False),
                      "value_type": "json"})
    if body.vocab is not None:
        vocab = normalize_vocab(body.vocab)
        items.append({"key": "vocab", "value": json.dumps(vocab, ensure_ascii=False),
                      "value_type": "json"})
    if items:
        await mgr.set_values(group.group_id, "profile", items)
    return await _profile_payload(group.group_id)


@router.post("/proposal/apply")
async def apply_enrich_proposal(group: Group = Depends(get_group_or_404)) -> dict:
    from fastapi import HTTPException

    from app.services.enrichment_service import apply_proposal
    result = await apply_proposal(group)
    if result is None:
        raise HTTPException(status_code=404, detail="적용할 제안이 없습니다")
    return await _profile_payload(group.group_id)


@router.post("/proposal/dismiss")
async def dismiss_enrich_proposal(group: Group = Depends(get_group_or_404)) -> dict:
    from app.services.enrichment_service import dismiss_proposal
    await dismiss_proposal(group)
    return await _profile_payload(group.group_id)
