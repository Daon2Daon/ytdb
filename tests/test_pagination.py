from app.routers.videos import _page_number


def test_page_number_first_page():
    assert _page_number(limit=20, offset=0) == 1


def test_page_number_third_page():
    assert _page_number(limit=20, offset=40) == 3


def test_page_number_zero_limit_defaults_to_one():
    assert _page_number(limit=0, offset=0) == 1
