"""그룹 삭제: 자동 생성 스키마 판별 + 삭제 라우트 동작."""

from app.routers.groups import is_auto_schema


def test_is_auto_schema_matches_generated_pattern():
    # create_group이 만드는 형태: youtube_u{user_id}_{token_hex(3)}
    assert is_auto_schema("youtube_u1_a1b2c3") is True
    assert is_auto_schema("youtube_u42_00ff00") is True


def test_is_auto_schema_rejects_custom_schemas():
    assert is_auto_schema("youtube_invest") is False        # 레거시/관리자 커스텀
    assert is_auto_schema("youtube_u1_xyz") is False        # hex 아님
    assert is_auto_schema("youtube_u1_a1b2c3d4") is False   # hex 길이 초과
    assert is_auto_schema("youtube_ua_a1b2c3") is False     # user_id 숫자 아님
    assert is_auto_schema("public") is False
