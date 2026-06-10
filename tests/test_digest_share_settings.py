from app.models.pg.digest import Digest


def test_digest_model_has_share_columns():
    cols = Digest.__table__.columns
    assert "share_token" in cols
    assert "share_visibility" in cols
    assert cols["share_token"].unique is True
