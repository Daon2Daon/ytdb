"""댓글 분석이 그룹·전역 설정을 저장소 규약대로 해석하는지 검증한다.

두 가지가 실제 운영에서 조용히 깨졌던 지점이다:
  - AI 게이트웨이는 그룹값이 비면 전역으로 폴백해야 한다(resolve_ai_gateway).
    get_ai_gateway는 그룹 스코프만 읽고 코드 기본값으로 떨어져 전역을 무시한다.
  - prompts.comment_analysis_prompt는 dataclass에만 있고 로더가 읽지 않으면
    그룹에 저장해도 항상 빈 문자열이 되어 기본 프롬프트만 쓰인다.
"""

import inspect


def test_orchestrator_uses_global_aware_gateway_resolver():
    from app.services import comment_analysis_service

    src = inspect.getsource(comment_analysis_service.run_comment_analysis)
    assert "resolve_ai_gateway(group.group_id)" in src
    # 그룹 스코프만 읽는 로더를 호출하면 전역 설정이 무시된다.
    assert "get_settings_manager().get_ai_gateway" not in src


def test_get_prompts_loads_comment_analysis_prompt():
    from app.services.settings_manager import SettingsManager

    src = inspect.getsource(SettingsManager.get_prompts)
    assert "comment_analysis_prompt" in src


def test_prompt_settings_has_comment_field():
    from app.services.settings_types import PromptSettings

    assert PromptSettings().comment_analysis_prompt == ""
