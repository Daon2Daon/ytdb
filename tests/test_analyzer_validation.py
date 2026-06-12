"""분석 응답 검증 완화 검증.

sentiment/confidence_score는 더 이상 필수가 아니며, 경제 enum도 강제하지 않는다.
그룹 성격에 따라 sentiment가 없거나 자유 값('trendy' 등)이어도 분석이 실패하지 않는다.
핵심 2개(one_line/short_summary_md)만 필수.

full_analysis_md는 레거시 필드로 더 이상 필수가 아니다. 현재 본문은 구조화
analysis_sections로 관리하며, LLM 프롬프트도 full_analysis_md를 요청하지 않는다.
(레거시 행은 build_sections 폴백이 처리한다.)
"""

import pytest

from app.services.analyzer import (
    AnalysisValidationError,
    _coerce_confidence,
    _validate,
)

ESSENTIAL = {
    "one_line": "한 줄",
    "short_summary_md": "요약",
}


def test_passes_with_only_essentials():
    _validate(dict(ESSENTIAL))  # sentiment/confidence/headline 없어도 통과


def test_passes_with_free_form_sentiment():
    _validate({**ESSENTIAL, "sentiment": "trendy"})  # 경제 enum 아니어도 통과
    _validate({**ESSENTIAL, "sentiment": "bullish"})


def test_passes_without_confidence_or_with_bad_confidence():
    _validate({**ESSENTIAL})  # confidence 없음
    _validate({**ESSENTIAL, "confidence_score": "high"})  # 비숫자
    _validate({**ESSENTIAL, "confidence_score": 1.5})  # 범위 밖


def test_raises_when_essential_missing():
    for key in ESSENTIAL:
        bad = {k: v for k, v in ESSENTIAL.items() if k != key}
        with pytest.raises(AnalysisValidationError):
            _validate(bad)


def test_coerce_confidence():
    assert _coerce_confidence(0.8) == 0.8
    assert _coerce_confidence("0.5") == 0.5
    assert _coerce_confidence("high") is None
    assert _coerce_confidence(1.5) is None
    assert _coerce_confidence(-0.1) is None
    assert _coerce_confidence(None) is None
