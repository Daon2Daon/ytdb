from app.models.pg.digest import Digest


def test_digest_model_has_share_columns():
    cols = Digest.__table__.columns
    assert "share_token" in cols
    assert "share_visibility" in cols
    assert cols["share_token"].unique is True


from app.services.settings_types import DigestSettings
from app.services.default_settings import DEFAULT_GROUP_SETTINGS


def test_digest_settings_default_share_link_enabled():
    assert DigestSettings().share_link_enabled is True


def test_default_seed_includes_share_link_enabled():
    keys = {i["key"] for i in DEFAULT_GROUP_SETTINGS["digest"]}
    assert "share_link_enabled" in keys
    item = next(i for i in DEFAULT_GROUP_SETTINGS["digest"] if i["key"] == "share_link_enabled")
    assert item["value"] == "true"
    assert item["value_type"] == "bool"
