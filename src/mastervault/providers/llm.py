"""LLM providers behind one Protocol.

- AnthropicLLM — Messages API; structured output via a forced tool call whose
  input_schema is the response model's JSON schema.
- OpenAILLM    — chat.completions; honors settings.llm.base_url so
  OpenAI-compatible gateways work; structured via response_format.
- MockLLM      — a response registry for tests and dry-run pipelines: enqueue
  responses per task name, FIFO pop, deterministic fallback when empty.

Tier ("small" | "medium" | "large") resolves through
settings.llm.model_small / model_medium / model_large. Structured-output
failures retry ONCE with the validation errors appended, then raise
StructuredOutputError. Secrets come only from the environment via Settings.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from mastervault.config import Settings

from .prices import cost as price_cost
from .prices import estimate_tokens

Tier = Literal["small", "medium", "large"]

_STRUCTURED_TOOL_NAME = "emit_structured"

# Chat-completions models known to accept response_format={"type": "json_schema"}.
_JSON_SCHEMA_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4")


class StructuredOutputError(RuntimeError):
    """The model failed to produce schema-valid output after one retry."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    parsed: BaseModel | None
    model: str
    usage_in: int
    usage_out: int
    cost_usd: float


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every LLM backend satisfies."""

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
        tier: Tier = "medium",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> LLMResult: ...


def resolve_model(settings: Settings, tier: Tier) -> str:
    """Map a tier name to the configured model id."""
    mapping = {
        "small": settings.llm.model_small,
        "medium": settings.llm.model_medium,
        "large": settings.llm.model_large,
    }
    try:
        return mapping[tier]
    except KeyError:
        raise ValueError(f"unknown tier: {tier!r} (expected small | medium | large)") from None


def _retry_prompt(prompt: str, errors: str) -> str:
    return (
        f"{prompt}\n\n"
        "Your previous response failed schema validation with these errors:\n"
        f"{errors}\n"
        "Respond again, matching the schema exactly."
    )


class AnthropicLLM:
    """Anthropic Messages API. Client injected or late-constructed."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    def _create(
        self,
        model: str,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None,
        response_model: type[BaseModel] | None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        if response_model is not None:
            kwargs["tools"] = [
                {
                    "name": _STRUCTURED_TOOL_NAME,
                    "description": "Emit the answer as structured data matching the schema.",
                    "input_schema": response_model.model_json_schema(),
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": _STRUCTURED_TOOL_NAME}
        return self._get_client().messages.create(**kwargs)

    @staticmethod
    def _tool_input(response: Any) -> dict[str, Any]:
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
        raise ValueError("response contained no tool_use block")

    @staticmethod
    def _text(response: Any) -> str:
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
        tier: Tier = "medium",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> LLMResult:
        model = resolve_model(self._settings, tier)
        usage_in = 0
        usage_out = 0

        def call(current_prompt: str) -> Any:
            nonlocal usage_in, usage_out
            response = self._create(
                model,
                current_prompt,
                max_tokens=max_tokens,
                system=system,
                response_model=response_model,
            )
            usage_in += response.usage.input_tokens
            usage_out += response.usage.output_tokens
            return response

        if response_model is None:
            response = call(prompt)
            text = self._text(response)
            return LLMResult(
                text=text,
                parsed=None,
                model=model,
                usage_in=usage_in,
                usage_out=usage_out,
                cost_usd=price_cost(model, usage_in, usage_out),
            )

        response = call(prompt)
        try:
            parsed = response_model.model_validate(self._tool_input(response))
        except (ValidationError, ValueError) as first_error:
            # Retry ONCE with the validation errors appended.
            response = call(_retry_prompt(prompt, str(first_error)))
            try:
                parsed = response_model.model_validate(self._tool_input(response))
            except (ValidationError, ValueError) as second_error:
                raise StructuredOutputError(
                    f"structured output failed after retry for task {task!r}: {second_error}"
                ) from second_error
        return LLMResult(
            text=parsed.model_dump_json(),
            parsed=parsed,
            model=model,
            usage_in=usage_in,
            usage_out=usage_out,
            cost_usd=price_cost(model, usage_in, usage_out),
        )


def _default_openai_client_factory(**kwargs: Any) -> Any:
    from openai import OpenAI

    return OpenAI(**kwargs)


class OpenAILLM:
    """OpenAI chat.completions, with optional base_url for compatible gateways.

    The client factory is injectable so tests can assert construction kwargs
    (in particular that settings.llm.base_url is plumbed through).
    """

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        *,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._client_factory = client_factory or _default_openai_client_factory

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(
                api_key=self._settings.openai_api_key,
                base_url=self._settings.llm.base_url,
            )
        return self._client

    @staticmethod
    def _supports_json_schema(model: str) -> bool:
        return model.startswith(_JSON_SCHEMA_MODEL_PREFIXES)

    def _messages(self, prompt: str, system: str | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
        tier: Tier = "medium",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> LLMResult:
        model = resolve_model(self._settings, tier)
        usage_in = 0
        usage_out = 0

        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
        effective_prompt = prompt
        if response_model is not None:
            schema = response_model.model_json_schema()
            if self._supports_json_schema(model):
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": response_model.__name__, "schema": schema},
                }
            else:
                # json_object mode: the schema travels in the prompt instead.
                kwargs["response_format"] = {"type": "json_object"}
                effective_prompt = (
                    f"{prompt}\n\nRespond with a JSON object matching this schema:\n"
                    f"{json.dumps(schema)}"
                )

        def call(current_prompt: str) -> str:
            nonlocal usage_in, usage_out
            response = self._get_client().chat.completions.create(
                messages=self._messages(current_prompt, system), **kwargs
            )
            usage_in += response.usage.prompt_tokens
            usage_out += response.usage.completion_tokens
            return response.choices[0].message.content or ""

        text = call(effective_prompt)
        parsed: BaseModel | None = None
        if response_model is not None:
            try:
                parsed = response_model.model_validate_json(text)
            except (ValidationError, ValueError) as first_error:
                text = call(_retry_prompt(effective_prompt, str(first_error)))
                try:
                    parsed = response_model.model_validate_json(text)
                except (ValidationError, ValueError) as second_error:
                    raise StructuredOutputError(
                        f"structured output failed after retry for task {task!r}: {second_error}"
                    ) from second_error
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            usage_in=usage_in,
            usage_out=usage_out,
            cost_usd=price_cost(model, usage_in, usage_out),
        )


def _schema_default(prop: dict[str, Any]) -> Any:
    if "default" in prop:
        return prop["default"]
    prop_type = prop.get("type")
    if isinstance(prop_type, list) and prop_type:
        prop_type = prop_type[0]
    return {
        "string": "",
        "integer": 0,
        "number": 0.0,
        "boolean": True,
        "array": [],
        "object": {},
        "null": None,
    }.get(prop_type)


def _default_instance(response_model: type[BaseModel], overrides: dict[str, Any]) -> BaseModel:
    """Best-effort instance of a response model with type-appropriate defaults."""
    schema = response_model.model_json_schema()
    data = {name: _schema_default(prop) for name, prop in schema.get("properties", {}).items()}
    data.update({key: value for key, value in overrides.items() if key in data})
    return response_model.model_validate(data)


class MockLLM:
    """Response registry for tests: push per-task, FIFO pop, recorded calls.

    When a task's queue is empty the fallback is deterministic:
    - task "judge" with a response_model -> a sufficient=true-shaped instance
    - otherwise -> a short extractive stub built from the prompt's last 200
      characters (parsed=None).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._queues: dict[str, deque[str | BaseModel]] = defaultdict(deque)
        self.calls: list[tuple[str, str]] = []

    def push(self, task: str, response: str | BaseModel) -> None:
        """Enqueue a canned response (raw text or a parsed model) for a task."""
        self._queues[task].append(response)

    def _model_for(self, tier: Tier) -> str:
        if self._settings is not None:
            return resolve_model(self._settings, tier)
        return f"mock-{tier}"

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
        tier: Tier = "medium",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> LLMResult:
        self.calls.append((task, prompt))
        parsed: BaseModel | None = None

        queue = self._queues[task]
        if queue:
            item = queue.popleft()
            if isinstance(item, BaseModel):
                parsed = item
                text = item.model_dump_json()
            else:
                text = item
                if response_model is not None:
                    try:
                        parsed = response_model.model_validate_json(text)
                    except (ValidationError, ValueError):
                        parsed = None
        elif task == "judge" and response_model is not None:
            parsed = _default_instance(response_model, {"sufficient": True})
            text = parsed.model_dump_json()
        else:
            text = f"MOCK[{task}]: {prompt[-200:]}"

        return LLMResult(
            text=text,
            parsed=parsed,
            model=self._model_for(tier),
            usage_in=estimate_tokens(prompt),
            usage_out=estimate_tokens(text),
            cost_usd=0.0,
        )


def get_llm(settings: Settings) -> LLMProvider:
    """Resolve settings.llm.provider to a concrete provider."""
    provider = settings.llm.provider
    if provider == "anthropic":
        return AnthropicLLM(settings)
    if provider == "openai":
        return OpenAILLM(settings)
    if provider == "mock":
        return MockLLM(settings)
    raise ValueError(f"unknown llm provider: {provider!r}")
