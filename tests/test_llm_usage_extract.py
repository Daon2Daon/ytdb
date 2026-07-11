"""LLM 응답 usage 추출 단위 테스트 (DB·네트워크 불필요)."""

from app.services.llm_client import (
    AnalyzerResult,
    ChatResult,
    _extract_chat_usage,
    _extract_gemini_usage,
)


def test_extract_gemini_usage_basic():
    payload = {"usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 340}}
    assert _extract_gemini_usage(payload) == (1200, 340)


def test_extract_gemini_usage_with_thoughts():
    # thinking 모델: 출력 = candidates + thoughts (과금 기준)
    payload = {"usageMetadata": {
        "promptTokenCount": 100, "candidatesTokenCount": 40, "thoughtsTokenCount": 60,
    }}
    assert _extract_gemini_usage(payload) == (100, 100)


def test_extract_gemini_usage_missing():
    assert _extract_gemini_usage({}) == (None, None)
    assert _extract_gemini_usage({"usageMetadata": {}}) == (None, None)
    assert _extract_gemini_usage({"usageMetadata": "broken"}) == (None, None)


def test_extract_chat_usage():
    payload = {"usage": {"prompt_tokens": 500, "completion_tokens": 200}}
    assert _extract_chat_usage(payload) == (500, 200)
    assert _extract_chat_usage({}) == (None, None)
    assert _extract_chat_usage({"usage": None}) == (None, None)


def test_result_dataclasses_have_usage_fields():
    a = AnalyzerResult(data={}, raw_text="")
    assert a.input_tokens is None and a.output_tokens is None
    c = ChatResult(content="", raw={})
    assert c.input_tokens is None and c.output_tokens is None
