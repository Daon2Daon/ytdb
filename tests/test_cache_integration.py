"""캐시 결과 → AnalysisPipelineResult 변환과 저장 함수 시그니처 검증."""

from app.services.analyzer import (
    PROMPT_VERSION,
    AnalysisPipelineResult,
    result_from_cache,
    save_analysis_to_group,
    save_tags_for_video,
)


def test_result_from_cache_shape():
    data = {"one_line": "요약", "short_summary_md": "본문", "tags": []}
    r = result_from_cache(data, model_name="gemini/gemini-2.5-flash", gateway_url="http://gw")
    assert isinstance(r, AnalysisPipelineResult)
    assert r.data == data
    assert r.route == "cache"
    assert r.model_name == "gemini/gemini-2.5-flash"
    assert r.gateway_url == "http://gw"
    assert r.prompt_version == PROMPT_VERSION


def test_module_level_save_functions_exist():
    # 캐시 적중 경로가 LLM 클라이언트 없이 호출할 수 있어야 한다(모듈 함수).
    import inspect

    assert inspect.iscoroutinefunction(save_analysis_to_group)
    assert inspect.iscoroutinefunction(save_tags_for_video)
    params = list(inspect.signature(save_analysis_to_group).parameters)
    assert params[:3] == ["session", "video_pk", "result"]


def test_build_analysis_pipeline_accepts_resolved_param():
    """빌더가 resolve_prompts 결과를 주입받을 수 있어야 한다(중복 조회 방지)."""
    import inspect

    from app.services.analyzer import build_analysis_pipeline

    params = inspect.signature(build_analysis_pipeline).parameters
    assert "resolved" in params
