"""공유 페이지 토큰 생성."""

from __future__ import annotations

import secrets

DEFAULT_VISIBILITY = "unlisted"


def generate_share_token() -> str:
    """추측 불가한 URL-safe 토큰."""
    return secrets.token_urlsafe(12)
