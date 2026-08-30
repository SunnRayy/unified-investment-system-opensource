from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.api.main import app

client = TestClient(app)

def test_download_ai_context():
    """GET /api/export/ai-context returns markdown file download."""
    with patch("src.api.routes.export.DatabaseConnector") as mock_db_cls, \
         patch("src.api.routes.export.MarkdownContextGenerator") as mock_gen_cls:
        
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        
        mock_generator = MagicMock()
        mock_generator.generate.return_value = "# Fake Markdown Context"
        mock_gen_cls.return_value = mock_generator

        response = client.get("/export/ai-context")
        
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "attachment; filename=Personal_Investment_Analysis_Context_" in response.headers["content-disposition"]
        assert response.text == "# Fake Markdown Context"
        mock_db.close.assert_called_once()
