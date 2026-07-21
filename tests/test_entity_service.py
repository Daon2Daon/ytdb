from app.services.entity_service import canon_key, pick_canonical_match


def test_canon_key_normalizes():
    assert canon_key("  SoftBank ") == "softbank"
    assert canon_key("소프트뱅크") == "소프트뱅크"


def test_pick_canonical_match_by_canonical():
    existing = [
        {"canonical_name": "SoftBank", "aliases": ["소프트뱅크"]},
        {"canonical_name": "KDDI", "aliases": []},
    ]
    assert pick_canonical_match("softbank", existing) == "SoftBank"


def test_pick_canonical_match_by_alias():
    existing = [{"canonical_name": "SoftBank", "aliases": ["소프트뱅크", "SB"]}]
    assert pick_canonical_match("sb", existing) == "SoftBank"


def test_pick_canonical_match_miss():
    existing = [{"canonical_name": "SoftBank", "aliases": []}]
    assert pick_canonical_match("라쿠텐", existing) is None
