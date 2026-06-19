from app.models.pg.digest import Digest


def test_digest_model_has_share_columns():
    cols = Digest.__table__.columns
    assert "share_token" in cols
    assert "share_visibility" in cols
    assert cols["share_token"].unique is True


from app.services.settings_types import DigestShareSettings
from app.services.default_settings import DEFAULT_GROUP_SETTINGS


def test_digest_share_settings_default():
    assert DigestShareSettings().share_link_enabled is True


def test_default_seed_uses_configs_json():
    keys = {i["key"] for i in DEFAULT_GROUP_SETTINGS["digest"]}
    assert "configs" in keys
    assert "share_link_enabled" in keys
    assert "period_weeks" not in keys
