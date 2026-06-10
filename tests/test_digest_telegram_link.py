from app.services.digest_service import build_digest_telegram_text, _build_digest_share_url


def test_share_url_built_when_token_and_base(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    url = _build_digest_share_url("eco", "tok123")
    assert url == "https://h/d/eco/tok123"


def test_share_url_empty_without_base(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "", raising=False)
    assert _build_digest_share_url("eco", "tok123") == ""


def test_text_includes_link_when_enabled(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token="tok123", share_link_enabled=True,
    )
    assert "<b>핵심임</b>" in text
    assert "요약임" in text
    assert 'https://h/d/eco/tok123' in text
    assert "웹에서 자세히 보기" in text


def test_text_excludes_link_when_disabled(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token="tok123", share_link_enabled=False,
    )
    assert "웹에서 자세히 보기" not in text


def test_text_excludes_link_when_no_token():
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token=None, share_link_enabled=True,
    )
    assert "웹에서 자세히 보기" not in text
