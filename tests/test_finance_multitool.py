"""Regression tests for finance multi-tool sequencing."""

import json

import pytest

from src.app.agents import base as base_module
from src.app.agents import finance as finance_module


class _Settings:
    openai_api_key = "test-key"
    openai_model = "subagent-model"
    default_tenant_id = "00000000-0000-0000-0000-000000000001"


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str, call_id: str = "call-1") -> None:
        self.id = call_id
        self.function = _FakeFunction(name=name, arguments=arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeCompletionResponse:
    def __init__(
        self,
        *,
        content: str | None,
        tool_calls: list[_FakeToolCall] | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content=content, tool_calls=tool_calls))]
        self.usage = _FakeUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


class _FakeChatCompletions:
    def __init__(self, responses: list[_FakeCompletionResponse]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeAsyncOpenAI:
    def __init__(self, responses: list[_FakeCompletionResponse]) -> None:
        self.chat = type("FakeChat", (), {"completions": _FakeChatCompletions(responses=responses)})()


@pytest.mark.asyncio
async def test_finance_chains_list_categories_then_register_expense(monkeypatch: pytest.MonkeyPatch):
    """If user intent is clear, finance should chain list->register in one turn."""
    monkeypatch.setattr(base_module, "get_settings", lambda: _Settings())

    responses = [
        _FakeCompletionResponse(
            content=None,
            tool_calls=[
                _FakeToolCall(
                    name="listar_categorias",
                    arguments=json.dumps({}),
                    call_id="call-list",
                )
            ],
            prompt_tokens=20,
            completion_tokens=5,
        ),
        _FakeCompletionResponse(
            content=None,
            tool_calls=[
                _FakeToolCall(
                    name="registrar_gasto",
                    arguments=json.dumps(
                        {
                            "amount": 85000,
                            "category": "Combustible",
                            "description": "combustible",
                        }
                    ),
                    call_id="call-expense",
                )
            ],
            prompt_tokens=25,
            completion_tokens=8,
        ),
    ]
    monkeypatch.setattr(
        finance_module,
        "AsyncOpenAI",
        lambda api_key: _FakeAsyncOpenAI(responses=responses),  # noqa: ARG005
    )

    agent = finance_module.FinanceAgent()

    async def _fake_check_first_time(phone: str) -> bool:  # noqa: ARG001
        return False

    monkeypatch.setattr(agent, "check_first_time", _fake_check_first_time)

    executed_tools: list[str] = []

    async def _fake_execute_tool(tool_name: str, args: dict, tenant_id: str, **kwargs):  # noqa: ARG001
        executed_tools.append(tool_name)
        if tool_name == "listar_categorias":
            return {
                "success": True,
                "data": {
                    "categories": [
                        {"name": "Combustible (Movilidad)"},
                        {"name": "Supermercado (Alimentación)"},
                    ]
                },
            }
        if tool_name == "registrar_gasto":
            return {
                "success": True,
                "data": {
                    "message": "✅ Registré un gasto de $85,000.\n📌 Lo asigné a Movilidad > Combustible."
                },
            }
        return {"success": False, "error": "unexpected tool"}

    monkeypatch.setattr(agent, "_execute_tool", _fake_execute_tool)

    result = await agent.process(
        message="85 mil pesos en combustible",
        phone="5491111111111",
        tenant_id="tenant-1",
        history=[],
    )

    assert executed_tools == ["listar_categorias", "registrar_gasto"]
    assert "Registré un gasto de $85,000" in result.response
