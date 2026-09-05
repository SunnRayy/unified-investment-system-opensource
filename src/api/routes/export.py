from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import io
from datetime import datetime
from src.database.connector import DatabaseConnector
from src.services.context_generator import MarkdownContextGenerator

router = APIRouter(prefix="/export", tags=["export"])

@router.get("/ai-context")
async def download_ai_context():
    """Generate and download Personal_Investment_Analysis_Context_*.md"""
    db = DatabaseConnector()
    try:
        generator = MarkdownContextGenerator(db)
        content = generator.generate()
    finally:
        db.close()

    filename = f"Personal_Investment_Analysis_Context_{datetime.now().strftime('%Y%m%d')}.md"

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
