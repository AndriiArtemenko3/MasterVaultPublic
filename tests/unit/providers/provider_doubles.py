"""Recording doubles for provider unit tests. No network, no real SDKs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeAPIError(Exception):
    """Duck-typed SDK error carrying a status_code, like openai.APIStatusError."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"fake api error {status_code}")
        self.status_code = status_code


class FakeOpenAIEmbeddingsClient:
    """Stands in for openai.OpenAI on the embeddings surface.

    Records every batch; optionally raises scripted exceptions first (each
    scripted failure still counts as an attempted call). Successful calls
    return one vector per text whose single component encodes the text's
    trailing index ("text-3" -> [3.0]) so order preservation is checkable.
    """

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._failures = list(failures or [])
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, input: list[str]) -> Any:
        self.calls.append(list(input))
        if self._failures:
            raise self._failures.pop(0)
        data = [
            SimpleNamespace(embedding=[float(int(text.rsplit("-", 1)[-1]))]) for text in input
        ]
        return SimpleNamespace(data=data)


def anthropic_tool_response(
    tool_input: dict[str, Any], *, usage_in: int = 100, usage_out: int = 20
) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=tool_input)],
        usage=SimpleNamespace(input_tokens=usage_in, output_tokens=usage_out),
    )


def anthropic_text_response(text: str, *, usage_in: int = 100, usage_out: int = 20) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=usage_in, output_tokens=usage_out),
    )


class FakeAnthropicClient:
    """Stands in for anthropic.Anthropic: scripted responses, recorded kwargs."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropicClient ran out of scripted responses")
        return self._responses.pop(0)


def openai_chat_response(text: str, *, usage_in: int = 80, usage_out: int = 15) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=usage_in, completion_tokens=usage_out),
    )


class FakeOpenAIChatClient:
    """Stands in for openai.OpenAI on the chat.completions surface."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeOpenAIChatClient ran out of scripted responses")
        return self._responses.pop(0)


class FakeCohereClient:
    """Stands in for cohere.ClientV2: returns scripted (index, score) pairs."""

    def __init__(self, results: list[tuple[int, float]]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    def rerank(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            results=[
                SimpleNamespace(index=index, relevance_score=score)
                for index, score in self._results
            ]
        )
