"""Tests for strategy memo CRUD endpoints (Batch 1 — V4.5)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.strategy import router as strategy_router
from src.classification.schema import create_classification_tables
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(strategy_router)


@pytest.fixture()
def memo_client():
    """
    Spin up an on-disk temp DuckDB so all connections (dependency-injected
    and those created directly by write routes via resolve_db_path) share the
    same database file.

    Key design: the get_db override creates a *new* read-only connection per
    call (not held open), so the write routes can open their own read/write
    connections without DuckDB WAL conflicts.
    """
    from src.api.dependencies import get_db

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = f.name

    # Remove stale file — DatabaseConnector will create it fresh
    os.unlink(db_path)

    # Bootstrap schema using a short-lived connection
    bootstrap = DatabaseConnector(db_path)
    initialize_schema(bootstrap)
    create_classification_tables(bootstrap)
    bootstrap.run_migrations()  # Adds V4.5 content column to strategy_memos
    bootstrap.close()

    # get_db override: yield a fresh read-only connection per request, then close it
    # so the file is not locked when write routes open their own rw connection
    def override_get_db():
        conn = DatabaseConnector(db_path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db

    # Patch resolve_db_path in the connector module so all local imports inside
    # route functions pick up the patched version.
    with patch("src.database.connector.resolve_db_path", return_value=db_path):
        yield TestClient(app)

    app.dependency_overrides.clear()
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

CHINESE_CONTENT = """# 2026-03-25 周度策略备忘录

2026-03-25

## 市场判断

当前市场环境需要防御性操作，降低仓位，降低风险。

## 关键指令

1. 减持高估值成长股
2. 增加现金比例
3. 加仓防御性资产
"""

LONG_CONTENT = "# Long Title\n\n" + ("A" * 600)
UNFORMATTED_CONTENT = (
    "全球资产配置与风险控制周度备忘录制定日期：2026-03-26目标资产：NVDA。"
    "这是后续说明，不应进入标题。"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_memo_auto_extracts_from_chinese_content(memo_client):
    """POST with Chinese markdown — title, date, bias, directives auto-extracted."""
    resp = memo_client.post("/strategy/memos", json={"content": CHINESE_CONTENT})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "2026-03-25 周度策略备忘录"
    assert data["date"] == "2026-03-25"
    # _extract_bias sees both "降低风险" (defensive) and "加仓" (offensive) → neutral
    assert data["bias"] in ("neutral", "defensive", "offensive")
    assert len(data["directives"]) > 0
    assert data["id"] is not None


def test_create_memo_explicit_date_wins(memo_client):
    """memo_date param beats any date embedded in the content."""
    content = "# My Memo\n\n2026-01-01 is in the content.\n\n* do something important"
    resp = memo_client.post(
        "/strategy/memos",
        json={"content": content, "memo_date": "2026-03-20"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["date"] == "2026-03-20"


def test_create_memo_skips_frontmatter_in_title(memo_client):
    """Front-matter block is skipped; the real H1 becomes the title."""
    content = "---\nauthor: Ray\ndate: 2026-03-25\n---\n# Real Title\n\nBody text here.\n\n* directive one"
    resp = memo_client.post("/strategy/memos", json={"content": content})
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "Real Title"


def test_create_memo_extracts_short_title_from_unformatted_single_line_content(memo_client):
    """Single-line memo text should stop title extraction at metadata markers."""
    resp = memo_client.post("/strategy/memos", json={"content": UNFORMATTED_CONTENT})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "全球资产配置与风险控制周度备忘录"
    assert len(data["title"]) <= 120


def test_create_memo_409_on_duplicate(memo_client):
    """Posting the same date+title twice returns 409 with existing_id."""
    content = "# Duplicate Memo\n\n2026-03-25\n\n* directive"
    resp1 = memo_client.post(
        "/strategy/memos", json={"content": content, "memo_date": "2026-03-25"}
    )
    assert resp1.status_code == 201, resp1.text
    existing_id = resp1.json()["id"]

    resp2 = memo_client.post(
        "/strategy/memos", json={"content": content, "memo_date": "2026-03-25"}
    )
    assert resp2.status_code == 409, resp2.text
    detail = resp2.json()["detail"]
    assert detail["existing_id"] == existing_id


def test_get_memo_by_id_returns_full_content(memo_client):
    """POST then GET /{id} — full content is returned."""
    resp = memo_client.post("/strategy/memos", json={"content": CHINESE_CONTENT})
    assert resp.status_code == 201
    memo_id = resp.json()["id"]

    get_resp = memo_client.get(f"/strategy/memos/{memo_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == memo_id
    assert data["content"] == CHINESE_CONTENT.strip()


def test_get_memos_list_truncates_content(memo_client):
    """GET /memos without include_content truncates content to 500 chars."""
    resp = memo_client.post("/strategy/memos", json={"content": LONG_CONTENT})
    assert resp.status_code == 201

    list_resp = memo_client.get("/strategy/memos")
    assert list_resp.status_code == 200
    memos = list_resp.json()["memos"]
    assert len(memos) >= 1
    # Content should be truncated
    content_val = memos[0]["content"]
    assert content_val is not None
    assert len(content_val) <= 500


def test_get_memos_list_include_content_full(memo_client):
    """GET /memos?include_content=true returns full content."""
    resp = memo_client.post("/strategy/memos", json={"content": LONG_CONTENT})
    assert resp.status_code == 201

    list_resp = memo_client.get("/strategy/memos?include_content=true")
    assert list_resp.status_code == 200
    memos = list_resp.json()["memos"]
    assert len(memos) >= 1
    content_val = memos[0]["content"]
    assert content_val is not None
    assert len(content_val) > 500


def test_update_memo_explicit_title_wins(memo_client):
    """PUT with both new content and explicit title — explicit title is kept."""
    create_resp = memo_client.post("/strategy/memos", json={"content": CHINESE_CONTENT})
    assert create_resp.status_code == 201
    memo_id = create_resp.json()["id"]

    new_content = "# Extracted Title\n\nSome body text.\n\n* new directive here"
    put_resp = memo_client.put(
        f"/strategy/memos/{memo_id}",
        json={"content": new_content, "title": "My Explicit Title"},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["title"] == "My Explicit Title"


def test_update_memo_reextracts_on_content_change(memo_client):
    """PUT with only new content — title re-extracted from content H1."""
    create_resp = memo_client.post("/strategy/memos", json={"content": CHINESE_CONTENT})
    assert create_resp.status_code == 201
    memo_id = create_resp.json()["id"]

    new_content = "# 新的标题\n\n新的内容说明。\n\n* 新指令一"
    put_resp = memo_client.put(
        f"/strategy/memos/{memo_id}",
        json={"content": new_content},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["title"] == "新的标题"


def test_delete_memo(memo_client):
    """POST → DELETE → GET returns 404."""
    create_resp = memo_client.post("/strategy/memos", json={"content": CHINESE_CONTENT})
    assert create_resp.status_code == 201
    memo_id = create_resp.json()["id"]

    del_resp = memo_client.delete(f"/strategy/memos/{memo_id}")
    assert del_resp.status_code == 204

    get_resp = memo_client.get(f"/strategy/memos/{memo_id}")
    assert get_resp.status_code == 404


def test_delete_memo_404_on_missing(memo_client):
    """DELETE on a nonexistent id returns 404."""
    resp = memo_client.delete("/strategy/memos/999999")
    assert resp.status_code == 404
