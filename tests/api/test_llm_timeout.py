import inspect


def test_llm_endpoints_are_not_coroutines():
    from src.api.routes.ai_advisor import generate_brief, generate_review, generate_review_questions

    assert not inspect.iscoroutinefunction(generate_brief), "generate_brief must be sync def, not async def"
    assert not inspect.iscoroutinefunction(generate_review), "generate_review must be sync def, not async def"
    assert not inspect.iscoroutinefunction(
        generate_review_questions
    ), "generate_review_questions must be sync def, not async def"


def test_llm_client_has_timeout(monkeypatch):
    import litellm

    calls = []

    def mock_completion(**kwargs):
        calls.append(kwargs)
        raise Exception("mock")

    monkeypatch.setattr(litellm, "completion", mock_completion)

    from src.services.llm_client import LLMClient

    client = LLMClient.__new__(LLMClient)
    client._primary = "gemini/gemini-2.5-flash"
    client._fallbacks = []
    client._temperature = 0.7
    client._max_tokens = 1000
    client._db_path = ":memory:"

    try:
        client.complete("system", "test", expect_json=False, report_type="brief")
    except Exception:
        pass

    assert len(calls) > 0
    assert "timeout" in calls[0], "litellm.completion() must be called with timeout parameter"
    assert calls[0]["timeout"] == 120
