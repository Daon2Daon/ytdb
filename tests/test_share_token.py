from app.services.share_token import generate_share_token


def test_token_is_urlsafe_and_unique():
    a = generate_share_token()
    b = generate_share_token()
    assert a != b
    assert len(a) >= 12
    assert all(c.isalnum() or c in "-_" for c in a)
