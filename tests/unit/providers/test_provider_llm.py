"""LLM providers: anthropic structured output + retry, openai base_url, MockLLM."""

from __future__ import annotations

import json

import pytest
from provider_doubles import (
    FakeAnthropicClient,
    FakeOpenAIChatClient,
    anthropic_text_response,
    anthropic_tool_response,
    openai_chat_response,
)
from pydantic import BaseModel

from mastervault.providers.llm import (
    AnthropicLLM,
    MockLLM,
    OpenAILLM,
    StructuredOutputError,
    get_llm,
    resolve_model,
)


class Extraction(BaseModel):
    title: str
    score: int


class JudgeVerdict(BaseModel):
    sufficient: bool
    reason: str = ""


def _anthropic_settings(make_settings):
    return make_settings(
        llm={
            "provider": "anthropic",
            "model_small": "claude-haiku-4-5-20251001",
            "model_medium": "claude-sonnet-5",
            "model_large": "claude-sonnet-5",
        }
    )


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------


def test_tier_resolves_through_settings(make_settings) -> None:
    settings = _anthropic_settings(make_settings)
    assert resolve_model(settings, "small") == "claude-haiku-4-5-20251001"
    assert resolve_model(settings, "medium") == "claude-sonnet-5"
    assert resolve_model(settings, "large") == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# AnthropicLLM
# ---------------------------------------------------------------------------


def test_anthropic_structured_happy_path(make_settings) -> None:
    settings = _anthropic_settings(make_settings)
    fake = FakeAnthropicClient([anthropic_tool_response({"title": "Doc", "score": 3})])
    provider = AnthropicLLM(settings, client=fake)

    result = provider.complete("extract", "Extract the doc", response_model=Extraction)

    assert isinstance(result.parsed, Extraction)
    assert result.parsed.title == "Doc"
    assert result.model == "claude-sonnet-5"
    assert result.usage_in == 100
    assert result.usage_out == 20
    assert result.cost_usd > 0.0
    assert len(fake.calls) == 1
    # Structured output travels as a forced tool call with the model's schema.
    call = fake.calls[0]
    assert call["tool_choice"]["type"] == "tool"
    assert call["tools"][0]["input_schema"] == Extraction.model_json_schema()


def test_anthropic_structured_retries_once_then_succeeds(make_settings) -> None:
    settings = _anthropic_settings(make_settings)
    fake = FakeAnthropicClient(
        [
            anthropic_tool_response({"title": "Doc"}),  # missing "score" -> invalid
            anthropic_tool_response({"title": "Doc", "score": 5}),
        ]
    )
    provider = AnthropicLLM(settings, client=fake)

    result = provider.complete("extract", "Extract the doc", response_model=Extraction)

    assert isinstance(result.parsed, Extraction)
    assert result.parsed.score == 5
    assert len(fake.calls) == 2
    # The retry prompt carries the validation errors back to the model.
    retry_prompt = fake.calls[1]["messages"][0]["content"]
    assert "failed schema validation" in retry_prompt
    assert "score" in retry_prompt
    # Usage accumulates across both attempts.
    assert result.usage_in == 200
    assert result.usage_out == 40


def test_anthropic_structured_raises_after_second_failure(make_settings) -> None:
    settings = _anthropic_settings(make_settings)
    fake = FakeAnthropicClient(
        [
            anthropic_tool_response({"bogus": True}),
            anthropic_tool_response({"still": "bogus"}),
        ]
    )
    provider = AnthropicLLM(settings, client=fake)

    with pytest.raises(StructuredOutputError):
        provider.complete("extract", "Extract the doc", response_model=Extraction)
    assert len(fake.calls) == 2  # exactly one retry, never more


def test_anthropic_plain_text_path(make_settings) -> None:
    settings = _anthropic_settings(make_settings)
    fake = FakeAnthropicClient([anthropic_text_response("a summary")])
    provider = AnthropicLLM(settings, client=fake)

    result = provider.complete("summarize", "Summarize", tier="small", system="be terse")

    assert result.text == "a summary"
    assert result.parsed is None
    assert result.model == "claude-haiku-4-5-20251001"
    assert fake.calls[0]["system"] == "be terse"
    assert "tools" not in fake.calls[0]


# ---------------------------------------------------------------------------
# OpenAILLM
# ---------------------------------------------------------------------------


def test_openai_base_url_plumbed_into_client_construction(
    make_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    settings = make_settings(
        llm={
            "provider": "openai",
            "base_url": "http://gateway.internal/v1",
            "model_small": "gpt-4o-mini",
            "model_medium": "gpt-4o-mini",
            "model_large": "gpt-4o",
        }
    )
    fake = FakeOpenAIChatClient([openai_chat_response("ok")])
    factory_kwargs: dict = {}

    def factory(**kwargs):
        factory_kwargs.update(kwargs)
        return fake

    provider = OpenAILLM(settings, client_factory=factory)
    result = provider.complete("summarize", "Summarize")

    assert factory_kwargs["base_url"] == "http://gateway.internal/v1"
    assert factory_kwargs["api_key"] == "sk-test-not-real"
    assert result.text == "ok"
    assert result.model == "gpt-4o-mini"


def test_openai_structured_uses_json_schema_response_format(make_settings) -> None:
    settings = make_settings(
        llm={"provider": "openai", "model_medium": "gpt-4o-mini"}
    )
    payload = json.dumps({"title": "Doc", "score": 2})
    fake = FakeOpenAIChatClient([openai_chat_response(payload)])
    provider = OpenAILLM(settings, client=fake)

    result = provider.complete("extract", "Extract", response_model=Extraction)

    assert isinstance(result.parsed, Extraction)
    response_format = fake.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == Extraction.model_json_schema()


def test_openai_structured_retry_once_then_raise(make_settings) -> None:
    settings = make_settings(llm={"provider": "openai", "model_medium": "gpt-4o-mini"})
    fake = FakeOpenAIChatClient(
        [openai_chat_response("not json"), openai_chat_response("{}")]
    )
    provider = OpenAILLM(settings, client=fake)

    with pytest.raises(StructuredOutputError):
        provider.complete("extract", "Extract", response_model=Extraction)
    assert len(fake.calls) == 2


def test_openai_json_object_fallback_for_unknown_model(make_settings) -> None:
    settings = make_settings(llm={"provider": "openai", "model_medium": "llama-3.3-70b"})
    payload = json.dumps({"title": "Doc", "score": 9})
    fake = FakeOpenAIChatClient([openai_chat_response(payload)])
    provider = OpenAILLM(settings, client=fake)

    result = provider.complete("extract", "Extract", response_model=Extraction)

    assert isinstance(result.parsed, Extraction)
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    # In json_object mode the schema travels inside the prompt.
    prompt = fake.calls[0]["messages"][-1]["content"]
    assert "schema" in prompt


# ---------------------------------------------------------------------------
# MockLLM
# ---------------------------------------------------------------------------


def test_mock_registry_pops_fifo() -> None:
    mock = MockLLM()
    mock.push("extract", "first")
    mock.push("extract", "second")

    assert mock.complete("extract", "p1").text == "first"
    assert mock.complete("extract", "p2").text == "second"


def test_mock_push_accepts_parsed_models() -> None:
    mock = MockLLM()
    mock.push("extract", Extraction(title="Doc", score=1))

    result = mock.complete("extract", "p", response_model=Extraction)
    assert isinstance(result.parsed, Extraction)
    assert result.parsed.title == "Doc"


def test_mock_judge_default_is_sufficient_true(make_settings) -> None:
    mock = MockLLM()
    result = mock.complete("judge", "is the corpus enough?", response_model=JudgeVerdict)
    assert isinstance(result.parsed, JudgeVerdict)
    assert result.parsed.sufficient is True


def test_mock_default_echoes_prompt_tail() -> None:
    mock = MockLLM()
    prompt = "x" * 300 + " the actual question"
    result = mock.complete("summarize", prompt)
    assert result.parsed is None
    assert result.text.startswith("MOCK[summarize]:")
    assert "the actual question" in result.text
    assert len(result.text) <= len("MOCK[summarize]: ") + 200


def test_mock_records_calls_and_costs_nothing() -> None:
    mock = MockLLM()
    mock.complete("a", "prompt-a")
    mock.complete("b", "prompt-b")
    assert mock.calls == [("a", "prompt-a"), ("b", "prompt-b")]
    assert mock.complete("c", "p").cost_usd == 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_get_llm_resolves_each_provider(make_settings) -> None:
    assert isinstance(get_llm(make_settings(llm={"provider": "anthropic"})), AnthropicLLM)
    assert isinstance(get_llm(make_settings(llm={"provider": "openai"})), OpenAILLM)
    assert isinstance(get_llm(make_settings(llm={"provider": "mock"})), MockLLM)
