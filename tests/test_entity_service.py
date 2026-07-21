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


from app.services.entity_service import parse_merge_response


def test_parse_merge_response_auto_vs_hold():
    raw = '''{"clusters": [
        {"canonical": "SoftBank", "aliases": ["소프트뱅크"], "confidence": "high"},
        {"canonical": "라쿠텐", "aliases": ["Rakuten"], "confidence": "low"}
    ]}'''
    auto, hold = parse_merge_response(raw)
    assert auto == [{"canonical": "SoftBank", "aliases": ["소프트뱅크"]}]
    assert hold == [{"canonical": "라쿠텐", "aliases": ["Rakuten"]}]


def test_parse_merge_response_bad_json():
    assert parse_merge_response("nope") == ([], [])


def test_parse_merge_response_skips_empty_aliases():
    raw = '{"clusters": [{"canonical": "A", "aliases": [], "confidence": "high"}]}'
    auto, hold = parse_merge_response(raw)
    assert auto == [] and hold == []
