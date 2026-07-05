"""B-0a 제어 평면 신규 모델(prompt_presets/analysis_cache/analysis_deliveries) 검증."""

from app.control_db import APP_SCHEMA, Base


def test_prompt_presets_registered():
    from app.models.control.prompt_preset import PromptPreset

    assert f"{APP_SCHEMA}.prompt_presets" in Base.metadata.tables
    cols = {c.name for c in PromptPreset.__table__.columns}
    assert {"preset_id", "name", "description", "analysis_prompt",
            "digest_prompt", "is_active", "created_at", "updated_at"} <= cols
