"""B-0a 제어 평면 신규 모델(prompt_presets/analysis_cache/analysis_deliveries) 검증."""

from app.control_db import APP_SCHEMA, Base


def test_prompt_presets_registered():
    from app.models.control.prompt_preset import PromptPreset

    assert f"{APP_SCHEMA}.prompt_presets" in Base.metadata.tables
    cols = {c.name for c in PromptPreset.__table__.columns}
    assert {"preset_id", "name", "description", "analysis_prompt",
            "digest_prompt", "is_active", "created_at", "updated_at"} <= cols


def test_analysis_cache_registered():
    from app.models.control.analysis_cache import AnalysisCache

    assert f"{APP_SCHEMA}.analysis_cache" in Base.metadata.tables
    cols = {c.name for c in AnalysisCache.__table__.columns}
    assert {"cache_id", "video_id", "preset_id", "model", "status", "analysis",
            "input_tokens", "output_tokens", "created_at", "completed_at"} <= cols
    # UNIQUE(video_id, preset_id, model)가 동시 분석 방지 락 역할 (스펙 §2.9)
    uniques = [
        {c.name for c in con.columns}
        for con in AnalysisCache.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"video_id", "preset_id", "model"} in uniques


def test_analysis_deliveries_registered():
    from app.models.control.analysis_delivery import AnalysisDelivery

    assert f"{APP_SCHEMA}.analysis_deliveries" in Base.metadata.tables
    cols = {c.name for c in AnalysisDelivery.__table__.columns}
    assert {"delivery_id", "user_id", "group_id", "cache_id", "created_at"} <= cols
