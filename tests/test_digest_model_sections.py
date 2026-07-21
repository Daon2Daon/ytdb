"""digest 모델·스키마 sections 컬럼 존재 확인."""

from __future__ import annotations

from app.models.pg.digest import Digest
from app.schemas.digest import DigestOut


def test_digest_model_has_digest_sections_column():
    assert "digest_sections" in Digest.__table__.columns


def test_digest_out_has_digest_sections_field():
    assert "digest_sections" in DigestOut.model_fields
