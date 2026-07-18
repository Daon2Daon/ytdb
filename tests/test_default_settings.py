"""그룹 생성 시 시드되는 추천 기본 설정 검증."""

from app.services.default_settings import DEFAULT_GROUP_SETTINGS

# 시크릿/접속 식별 정보는 절대 시드하지 않는다.
BANNED_KEYS = {
    "api_key", "password", "bot_token", "youtube_api_key",
    "host", "dbname", "username", "chat_ids",
}
VALID_TYPES = {"string", "int", "float", "bool", "json"}


def test_no_secret_or_identity_keys_seeded():
    for category, items in DEFAULT_GROUP_SETTINGS.items():
        for item in items:
            assert item["key"] not in BANNED_KEYS, f"{category}.{item['key']} must not be seeded"


def test_all_value_types_valid():
    for items in DEFAULT_GROUP_SETTINGS.values():
        for item in items:
            assert item["value_type"] in VALID_TYPES
            assert isinstance(item["value"], str)


def test_recommended_defaults_present():
    ai = {i["key"]: i["value"] for i in DEFAULT_GROUP_SETTINGS["ai_gateway"]}
    assert ai["temperature"] == "0.3"
    assert ai["max_tokens"] == "8192"
    # base_url/primary_model은 시드 금지 — 그룹 명시값이 전역 폴백을 영구히
    # 가리는 회귀(2026-07-18) 방지. 전역/코드 기본값이 대신 적용된다.
    assert "base_url" not in ai
    assert "primary_model" not in ai
    db = {i["key"]: i["value"] for i in DEFAULT_GROUP_SETTINGS["database"]}
    assert db["sslmode"] == "prefer" and db["port"] == "5432"
    notif = {i["key"]: i["value"] for i in DEFAULT_GROUP_SETTINGS["notification"]}
    assert notif["parse_mode"] == "HTML"
    # 기본 분석 프롬프트가 비어있지 않게 시드된다.
    prompts = {i["key"]: i["value"] for i in DEFAULT_GROUP_SETTINGS["prompts"]}
    assert prompts["analysis_prompt"].strip()
