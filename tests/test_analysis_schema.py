from app.schemas.video import AnalysisOut


def test_analysis_out_exposes_sections():
    fields = AnalysisOut.model_fields
    assert "analysis_sections" in fields
