import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.routes import ai_advisor as ai_advisor_routes
from src.services.ai_advisor import context_builder as context_builder_module
from src.services.ai_advisor.brief_generator import BriefResult
from src.services.ai_advisor.review_generator import ReviewResult


@pytest.fixture
def ai_advisor_client(tmp_path, monkeypatch):
    db_path = tmp_path / "ai_advisor.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE ai_reports (
            id INTEGER,
            report_type VARCHAR,
            title VARCHAR,
            model_used VARCHAR,
            content_json VARCHAR,
            content_markdown VARCHAR,
            context_config_json VARCHAR,
            period_start DATE,
            period_end DATE,
            created_at TIMESTAMP,
            prompt_text VARCHAR,
            raw_response_text VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_reports (
            id, report_type, title, model_used, content_json, content_markdown,
            context_config_json, period_start, period_end, created_at,
            prompt_text, raw_response_text
        )
        VALUES (
            7, 'review', 'March Review', 'gemini/gemini-2.5-flash',
            '{"summary":{"narrative":"ok"}}',
            '# review',
            '{"tiers":{"portfolio":{"enabled":true,"detail":"summary"}}}',
            '2026-03-01', '2026-03-31', '2026-03-20 08:00:00',
            'review prompt text',
            'review raw response text'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_reports (
            id, report_type, title, model_used, content_json, content_markdown,
            context_config_json, period_start, period_end, created_at,
            prompt_text, raw_response_text
        )
        VALUES (
            6, 'brief', 'March Brief', 'gemini/gemini-2.5-flash',
            '{"宏观形势":{"narrative":"ok"}}',
            '# brief',
            '{"tiers":{"portfolio":{"enabled":true,"detail":"summary"}}}',
            NULL, NULL, '2026-03-20 09:00:00',
            'brief prompt text',
            'brief raw response text'
        )
        """
    )
    conn.close()

    monkeypatch.setattr(ai_advisor_routes, "_DB_PATH", Path(db_path))
    app = FastAPI()
    app.include_router(ai_advisor_routes.router)
    return TestClient(app)


def test_get_review_by_id_returns_debug_fields(ai_advisor_client):
    response = ai_advisor_client.get("/ai-advisor/review/7")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 7
    assert data["period_start"] == "2026-03-01"
    assert data["period_end"] == "2026-03-31"
    assert data["prompt_text"] == "review prompt text"
    assert data["raw_response_text"] == "review raw response text"


def test_get_review_history_returns_saved_reviews(ai_advisor_client):
    response = ai_advisor_client.get("/ai-advisor/review/history?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == 7
    assert data[0]["title"] == "March Review"
    assert data[0]["period_start"] == "2026-03-01"
    assert data[0]["period_end"] == "2026-03-31"


def test_update_review_updates_title_and_structured_content(ai_advisor_client):
    updated_content = {
        "交易汇总": {"narrative": "更新后的交易汇总"},
        "建议准确性": {"narrative": "更新后的建议准确性"},
        "组合表现": {"narrative": "更新后的组合表现"},
        "经验沉淀": {"narrative": "更新后的经验沉淀"},
        "准则更新建议": {"narrative": "更新后的准则更新建议"},
    }

    response = ai_advisor_client.put(
        "/ai-advisor/review/7",
        json={
            "title": "Updated March Review",
            "content_json": updated_content,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated March Review"
    # The editor posted LEGACY Chinese keys (a payload a user could still paste
    # from an old review). The adapter maps them on the way in, so the response
    # comes back under the stable IDs and the markdown uses the resolved label.
    assert data["content_json"]["trade_summary"]["narrative"] == "更新后的交易汇总"
    assert "## Trade summary" in data["content_markdown"]
    assert "更新后的建议准确性" in data["content_markdown"]

    persisted = ai_advisor_client.get("/ai-advisor/review/7")
    assert persisted.status_code == 200
    assert persisted.json()["title"] == "Updated March Review"
    assert persisted.json()["content_json"]["lessons_learned"]["narrative"] == "更新后的经验沉淀"


def test_delete_review_removes_saved_record(ai_advisor_client):
    response = ai_advisor_client.delete("/ai-advisor/review/7")

    assert response.status_code == 204

    missing = ai_advisor_client.get("/ai-advisor/review/7")
    assert missing.status_code == 404

    history = ai_advisor_client.get("/ai-advisor/review/history?limit=5")
    assert history.status_code == 200
    assert history.json() == []


def test_get_brief_by_id_returns_debug_fields(ai_advisor_client):
    response = ai_advisor_client.get("/ai-advisor/brief/6")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 6
    assert data["prompt_text"] == "brief prompt text"
    assert data["raw_response_text"] == "brief raw response text"


def test_get_review_by_id_returns_404_for_missing_review(ai_advisor_client):
    response = ai_advisor_client.get("/ai-advisor/review/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Review 999 not found"


def test_context_preview_uses_transactions_detail_and_falls_back(ai_advisor_client, monkeypatch):
    class FakeContextBuilder:
        def estimate_tokens(self, config):
            return config

    monkeypatch.setattr(context_builder_module, "ContextBuilder", FakeContextBuilder)

    response = ai_advisor_client.get(
        "/ai-advisor/context/preview?tiers=transactions&detail_portfolio=summary&detail_transactions=full"
    )
    assert response.status_code == 200
    assert response.json()["transactions"]["detail"] == "full"

    fallback_response = ai_advisor_client.get(
        "/ai-advisor/context/preview?tiers=transactions&detail_portfolio=compact"
    )
    assert fallback_response.status_code == 200
    assert fallback_response.json()["transactions"]["detail"] == "summary"


def test_context_render_returns_context_text(ai_advisor_client, monkeypatch):
    class FakeContextBuilder:
        def estimate_tokens(self, config):
            return {"total": 321}

        def build_identity_context(self, detail="summary"):
            return "## Identity"

        def build_portfolio_context(self, detail="summary", include_non_rebalanceable=False):
            return "## Portfolio"

        def build_market_context(self, detail="summary"):
            return "## Market"

        def build_strategy_context(self, detail="summary"):
            return ""

        def build_transactions_context(self, timeframe="14d"):
            return "## Transactions"

        def build_realtime_context(self):
            return ""

        def build_review_trade_summary(self, period_start, period_end):
            return "2026-03-01 | Microsoft Corp (MSFT) | Buy | qty=10 | price=USD 100.45 | grade=A"

    monkeypatch.setattr(context_builder_module, "ContextBuilder", FakeContextBuilder)

    response = ai_advisor_client.post(
        "/ai-advisor/context/render",
        json={
            "report_type": "brief",
            "context_config": {
                "tiers": {
                    "identity": {"enabled": True, "detail": "summary"},
                    "portfolio": {"enabled": True, "detail": "summary"},
                    "market": {"enabled": False, "detail": "summary"},
                    "strategy": {"enabled": False, "detail": "summary"},
                    "transactions": {"enabled": False, "detail": "summary", "timeframe": "14d"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "## Identity" in data["context_text"]
    assert "## Portfolio" in data["context_text"]
    assert data["token_estimate"]["total"] == 321


def test_context_render_returns_full_review_prompt_draft(ai_advisor_client, monkeypatch):
    class FakeContextBuilder:
        def estimate_tokens(self, config):
            return {"total": 654}

        def build_identity_context(self, detail="summary"):
            return "## Identity"

        def build_portfolio_context(self, detail="summary", include_non_rebalanceable=False):
            return "## Portfolio"

        def build_market_context(self, detail="summary"):
            return "## Market"

        def build_strategy_context(self, detail="summary"):
            return ""

        def build_transactions_context(self, timeframe="14d"):
            return "## Transactions"

        def build_realtime_context(self):
            return ""

        def build_review_trade_summary(self, period_start, period_end):
            return "2026-03-01 | Microsoft Corp (MSFT) | Buy | qty=10 | price=USD 100.45 | grade=A"

    monkeypatch.setattr(context_builder_module, "ContextBuilder", FakeContextBuilder)

    response = ai_advisor_client.post(
        "/ai-advisor/context/render",
        json={
            "report_type": "review",
            "context_config": {
                "tiers": {
                    "identity": {"enabled": True, "detail": "summary"},
                    "portfolio": {"enabled": True, "detail": "summary"},
                    "market": {"enabled": False, "detail": "summary"},
                    "strategy": {"enabled": False, "detail": "summary"},
                    "transactions": {"enabled": False, "detail": "summary", "timeframe": "30d"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
            "period_start": "2026-03-01",
            "period_end": "2026-03-31",
            "questions_answers": [
                {"question": "Why did you trade?", "answer": "Because it fit the plan."}
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "Trade records (2026-03-01 to 2026-03-31):" in data["context_text"]
    assert "Microsoft Corp (MSFT)" in data["context_text"]
    assert "Q: Why did you trade?" in data["context_text"]
    assert "A: Because it fit the plan." in data["context_text"]
    assert data["token_estimate"]["total"] == 654


def test_context_render_flattens_nested_config_for_token_estimate(ai_advisor_client, monkeypatch):
    class FakeContextBuilder:
        def estimate_tokens(self, config):
            return config

        def build_identity_context(self, detail="summary"):
            return ""

        def build_portfolio_context(self, detail="summary", include_non_rebalanceable=False):
            return "## Portfolio"

        def build_market_context(self, detail="summary"):
            return ""

        def build_strategy_context(self, detail="summary"):
            return ""

        def build_transactions_context(self, timeframe="14d", detail="summary"):
            return ""

        def build_realtime_context(self):
            return ""

        def build_review_trade_summary(self, period_start, period_end):
            return None

    monkeypatch.setattr(context_builder_module, "ContextBuilder", FakeContextBuilder)

    response = ai_advisor_client.post(
        "/ai-advisor/context/render",
        json={
            "report_type": "brief",
            "context_config": {
                "tiers": {
                    "portfolio": {"enabled": True, "detail": "detailed"},
                    "transactions": {"enabled": True, "detail": "full", "timeframe": "1y"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_estimate"]["portfolio"]["enabled"] is True
    assert data["token_estimate"]["portfolio"]["detail"] == "detailed"
    assert data["token_estimate"]["transactions"]["enabled"] is True
    assert data["token_estimate"]["transactions"]["timeframe"] == "1y"


def test_brief_generate_accepts_reviewed_context_text(ai_advisor_client, monkeypatch):
    captured = {}

    def fake_generate(self, context_config, db_path="data/unified.duckdb", reviewed_context_text=None, language=None):
        captured["context_config"] = context_config
        captured["reviewed_context_text"] = reviewed_context_text
        captured["language"] = language
        return BriefResult(
            id=1,
            report_type="brief",
            content_json={"宏观形势": {"narrative": "ok"}},
            content_markdown="# brief",
            model_used="gemini/gemini-2.5-flash",
            created_at="2026-03-20T12:00:00",
            context_config=context_config,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            prompt_text=reviewed_context_text,
            raw_response_text='{"宏观形势":{"narrative":"ok"}}',
        )

    monkeypatch.setattr("src.services.ai_advisor.brief_generator.BriefGenerator.generate", fake_generate)

    response = ai_advisor_client.post(
        "/ai-advisor/brief/generate",
        json={
            "context_config": {
                "tiers": {
                    "identity": {"enabled": True, "detail": "summary"},
                    "portfolio": {"enabled": True, "detail": "summary"},
                    "market": {"enabled": False, "detail": "summary"},
                    "strategy": {"enabled": False, "detail": "summary"},
                    "transactions": {"enabled": False, "detail": "summary", "timeframe": "14d"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
            "reviewed_context_text": "## Reviewed Context",
        },
    )

    assert response.status_code == 200
    assert captured["reviewed_context_text"] == "## Reviewed Context"


def test_brief_generate_returns_503_when_llm_models_fail(ai_advisor_client, monkeypatch):
    from src.services.llm_client import LLMAllModelsFailedError

    def fake_generate(self, context_config, db_path="data/unified.duckdb", reviewed_context_text=None, language=None):
        raise LLMAllModelsFailedError("All LLM models failed. Last error: upstream 503")

    monkeypatch.setattr("src.services.ai_advisor.brief_generator.BriefGenerator.generate", fake_generate)

    response = ai_advisor_client.post(
        "/ai-advisor/brief/generate",
        json={
            "context_config": {
                "tiers": {
                    "identity": {"enabled": True, "detail": "summary"},
                    "portfolio": {"enabled": True, "detail": "summary"},
                    "market": {"enabled": False, "detail": "summary"},
                    "strategy": {"enabled": False, "detail": "summary"},
                    "transactions": {"enabled": False, "detail": "summary", "timeframe": "14d"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
            "reviewed_context_text": "## Reviewed Context",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "All LLM models failed. Last error: upstream 503"


def test_review_generate_accepts_reviewed_context_text(ai_advisor_client, monkeypatch):
    captured = {}

    def fake_generate_review(
        self,
        questions_answers,
        period_start,
        period_end,
        context_config,
        db_path="data/unified.duckdb",
        reviewed_context_text=None,
        language=None,
    ):
        captured["reviewed_context_text"] = reviewed_context_text
        captured["language"] = language
        return ReviewResult(
            id=8,
            report_type="review",
            content_json={"交易汇总": {"narrative": "ok"}},
            content_markdown="# review",
            model_used="gemini/gemini-2.5-flash",
            created_at="2026-03-20T12:00:00",
            period_start=period_start,
            period_end=period_end,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            prompt_text=reviewed_context_text,
            raw_response_text='{"交易汇总":{"narrative":"ok"}}',
        )

    monkeypatch.setattr("src.services.ai_advisor.review_generator.ReviewGenerator.generate_review", fake_generate_review)

    response = ai_advisor_client.post(
        "/ai-advisor/review/generate",
        json={
            "questions_answers": [{"question": "Q1", "answer": "A1"}],
            "period_start": "2026-03-01",
            "period_end": "2026-03-31",
            "context_config": {
                "tiers": {
                    "identity": {"enabled": True, "detail": "summary"},
                    "portfolio": {"enabled": True, "detail": "summary"},
                    "market": {"enabled": False, "detail": "summary"},
                    "strategy": {"enabled": False, "detail": "summary"},
                    "transactions": {"enabled": False, "detail": "summary", "timeframe": "14d"},
                },
                "include_realtime": False,
                "include_non_rebalanceable": False,
            },
            "reviewed_context_text": "## Reviewed Review Context",
        },
    )

    assert response.status_code == 200
    assert captured["reviewed_context_text"] == "## Reviewed Review Context"


# ---------------------------------------------------------------------------
# Program BIL / WS-5 — legacy rows must keep rendering
# ---------------------------------------------------------------------------
#
# 39 briefs and 4 reviews on the owner's database are stored with Chinese
# section keys and are NEVER rewritten. Every endpoint that hands content_json
# back has to map them at read time, or the frontend receives keys it cannot
# match and the analysis is effectively lost.


_LEGACY_BRIEF_JSON = (
    '{"宏观形势":{"narrative":"全球市场稳定。","key_factors":["美联储政策"]},'
    '"持仓分析与风险预警":{"narrative":"持仓结构合理。",'
    '"positions":[{"name":"US_STK_AAPL","status":"持有","comment":"表现稳健"}]},'
    '"风险预警汇总":{"narrative":"当前风险可控。",'
    '"items":[{"title":"汇率风险","severity":"medium","description":"美元波动较大"}]},'
    '"操作建议":{"narrative":"维持现有仓位。",'
    '"actions":[{"asset":"US_STK_AAPL","action":"买入","reasoning":"估值合理"}]},'
    '"明日关注":{"narrative":"关注美联储声明。",'
    '"watchlist":[{"item":"美联储声明","trigger":"利率决议","level":"N/A"}]}}'
)

_LEGACY_REVIEW_JSON = (
    '{"交易汇总":{"narrative":"本期共2笔交易。"},'
    '"建议准确性":{"narrative":"建议大体正确。",'
    '"scorecard":[{"decision":"清仓中欧医疗","accuracy_tier":"高准确度"}]},'
    '"组合表现":{"narrative":"组合小幅跑赢。"},'
    '"经验沉淀":{"narrative":"本期主要经验。","lessons":["及时止损是关键"]},'
    '"准则更新建议":{"narrative":"建议更新准则。","suggestions":["单票仓位不超过15%"]}}'
)


@pytest.fixture
def legacy_rows_client(ai_advisor_client, tmp_path):
    """Overwrite the fixture rows with REAL pre-BIL payload shapes."""
    conn = duckdb.connect(str(tmp_path / "ai_advisor.duckdb"))
    conn.execute(
        "UPDATE ai_reports SET content_json = ? WHERE id = 6", [_LEGACY_BRIEF_JSON]
    )
    conn.execute(
        "UPDATE ai_reports SET content_json = ? WHERE id = 7", [_LEGACY_REVIEW_JSON]
    )
    conn.close()
    return ai_advisor_client


@pytest.mark.parametrize("path", ["/ai-advisor/brief/latest", "/ai-advisor/brief/6"])
def test_legacy_chinese_keyed_brief_is_adapted_on_read(legacy_rows_client, path):
    data = legacy_rows_client.get(path).json()
    content = data["content_json"]

    assert set(content) == {
        "macro_outlook",
        "holdings_risk",
        "risk_alerts",
        "action_items",
        "watchlist",
    }
    # Prose is untouched — only identity changed.
    assert content["macro_outlook"]["narrative"] == "全球市场稳定。"
    assert content["watchlist"]["watchlist"][0]["item"] == "美联储声明"
    # Legacy enum VALUES normalize too, or the badges lose their colour.
    assert content["holdings_risk"]["positions"][0]["status"] == "hold"
    assert content["action_items"]["actions"][0]["action"] == "buy"


@pytest.mark.parametrize("path", ["/ai-advisor/review/latest", "/ai-advisor/review/7"])
def test_legacy_chinese_keyed_review_is_adapted_on_read(legacy_rows_client, path):
    content = legacy_rows_client.get(path).json()["content_json"]

    assert set(content) == {
        "trade_summary",
        "advice_accuracy",
        "portfolio_performance",
        "lessons_learned",
        "rule_updates",
    }
    assert content["lessons_learned"]["lessons"] == ["及时止损是关键"]
    assert content["advice_accuracy"]["scorecard"][0]["accuracy_tier"] == "high"


def test_title_only_edit_does_not_rewrite_the_stored_legacy_keys(legacy_rows_client, tmp_path):
    """A read-time adapter, not a migration by side effect.

    PUT rewrites content_json even for a title-only edit. If it wrote the ADAPTED
    payload, the first rename of any old review would silently migrate its keys —
    exactly the destructive rewrite this design refuses.
    """
    response = legacy_rows_client.put("/ai-advisor/review/7", json={"title": "Renamed"})
    assert response.status_code == 200
    # The RESPONSE is adapted…
    assert "trade_summary" in response.json()["content_json"]

    # …while the STORED row still holds the original Chinese keys.
    conn = duckdb.connect(str(tmp_path / "ai_advisor.duckdb"), read_only=True)
    try:
        stored = conn.execute("SELECT content_json FROM ai_reports WHERE id = 7").fetchone()[0]
    finally:
        conn.close()
    assert "交易汇总" in stored
    assert "trade_summary" not in stored


def test_legacy_gate_goes_red_without_the_adapter(legacy_rows_client, monkeypatch):
    """Anti-vacuity: neutralise the adapter and the legacy row must stop resolving."""
    monkeypatch.setattr(
        ai_advisor_routes, "adapt_stored_content_json", lambda payload: payload or {}
    )
    content = legacy_rows_client.get("/ai-advisor/brief/latest").json()["content_json"]
    assert "macro_outlook" not in content, (
        "the legacy row resolved even with the adapter disabled — the gate above "
        "proves nothing"
    )
    assert "宏观形势" in content
