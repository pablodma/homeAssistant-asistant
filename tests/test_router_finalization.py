"""Tests for hybrid router finalization flow."""

import json

import pytest

from src.app.agents import base as base_module
from src.app.agents import router as router_module
from src.app.agents.base import AgentResult


class _Settings:
    openai_api_key = "test-key"
    openai_router_model = "router-model"
    orchestrator_finalizer_enabled = True
    orchestrator_finalize_on_multi_agent_only = False
    orchestrator_finalizer_model = "finalizer-model"


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
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
    def __init__(
        self,
        responses: list[_FakeCompletionResponse],
        *,
        fail_on_call_number: int | None = None,
    ) -> None:
        self._responses = responses
        self._fail_on_call_number = fail_on_call_number
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        call_number = len(self.calls)
        if self._fail_on_call_number is not None and call_number == self._fail_on_call_number:
            raise RuntimeError("forced finalizer failure")
        return self._responses.pop(0)


class _FakeAsyncOpenAI:
    def __init__(
        self,
        responses: list[_FakeCompletionResponse],
        *,
        fail_on_call_number: int | None = None,
    ) -> None:
        self.chat = type(
            "FakeChat",
            (),
            {
                "completions": _FakeChatCompletions(
                    responses=responses,
                    fail_on_call_number=fail_on_call_number,
                )
            },
        )()


class _StaticSubAgent:
    def __init__(self, result: AgentResult) -> None:
        self._result = result

    async def process(self, **kwargs) -> AgentResult:  # noqa: ARG002
        return self._result


def _build_router(
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: list[_FakeCompletionResponse],
    fail_on_call_number: int | None = None,
) -> router_module.RouterAgent:
    monkeypatch.setattr(base_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        router_module,
        "AsyncOpenAI",
        lambda api_key: _FakeAsyncOpenAI(  # noqa: ARG005
            responses=responses,
            fail_on_call_number=fail_on_call_number,
        ),
    )
    return router_module.RouterAgent()


@pytest.mark.asyncio
async def test_router_passthrough_when_single_deterministic_result(monkeypatch: pytest.MonkeyPatch):
    """Router should keep passthrough mode for simple deterministic results."""
    initial_response = _FakeCompletionResponse(
        content=None,
        tool_calls=[
            _FakeToolCall(
                name="finance_agent",
                arguments=json.dumps({"user_request": "Registrá gasto de 1000 en supermercado"}),
            )
        ],
        prompt_tokens=12,
        completion_tokens=4,
    )
    router = _build_router(monkeypatch, responses=[initial_response])

    finance_result = AgentResult(
        response="Listo, registré el gasto de 1000 en supermercado.",
        agent_used="finance",
        metadata={"tool": "registrar_gasto", "result": {"success": True}},
        response_type="deterministic",
        risk_level="low",
    )
    monkeypatch.setattr(
        router,
        "_get_sub_agent",
        lambda agent_name: _StaticSubAgent(finance_result) if agent_name == "finance" else None,
    )

    result = await router.process(
        message="Gasté 1000 en supermercado",
        phone="5491111111111",
        tenant_id="tenant-1",
        history=[],
    )

    assert result.response == "Listo, registré el gasto de 1000 en supermercado."
    assert result.metadata is not None
    assert result.metadata["response_mode"] == "passthrough"
    assert result.metadata["finalizer_attempted"] is False
    assert result.metadata["finalizer_fallback_used"] is False
    assert result.tokens_in == 12
    assert result.tokens_out == 4


@pytest.mark.asyncio
async def test_router_finalizes_when_multiple_sub_agents(monkeypatch: pytest.MonkeyPatch):
    """Router should run finalizer when multiple sub-agents respond."""
    initial_response = _FakeCompletionResponse(
        content=None,
        tool_calls=[
            _FakeToolCall(
                name="finance_agent",
                arguments=json.dumps({"user_request": "Gasté 5000 en nafta"}),
            ),
            _FakeToolCall(
                name="shopping_agent",
                arguments=json.dumps({"user_request": "Agregá leche a la lista"}),
            ),
        ],
        prompt_tokens=20,
        completion_tokens=6,
    )
    finalizer_response = _FakeCompletionResponse(
        content="Perfecto: registré el gasto de nafta y agregué leche a tu lista.",
        tool_calls=None,
        prompt_tokens=8,
        completion_tokens=5,
    )
    router = _build_router(monkeypatch, responses=[initial_response, finalizer_response])

    sub_agents = {
        "finance": _StaticSubAgent(
            AgentResult(
                response="Registré gasto de 5000 en nafta.",
                agent_used="finance",
                metadata={"tool": "registrar_gasto", "result": {"success": True}},
                response_type="deterministic",
                risk_level="low",
            )
        ),
        "shopping": _StaticSubAgent(
            AgentResult(
                response="Agregué leche a la lista.",
                agent_used="shopping",
                metadata={"tool": "agregar_item", "result": {"success": True}},
                response_type="deterministic",
                risk_level="low",
            )
        ),
    }
    monkeypatch.setattr(router, "_get_sub_agent", lambda agent_name: sub_agents.get(agent_name))

    result = await router.process(
        message="Gasté 5000 en nafta y agregá leche",
        phone="5491111111111",
        tenant_id="tenant-1",
        history=[],
    )

    assert result.response == "Perfecto: registré el gasto de nafta y agregué leche a tu lista."
    assert result.metadata is not None
    assert result.metadata["response_mode"] == "orchestrator_finalized"
    assert result.metadata["finalizer_attempted"] is True
    assert result.metadata["finalizer_fallback_used"] is False
    assert result.tokens_in == 28
    assert result.tokens_out == 11


@pytest.mark.asyncio
async def test_router_falls_back_to_passthrough_if_finalizer_fails(monkeypatch: pytest.MonkeyPatch):
    """If finalizer fails, router must return passthrough response safely."""
    initial_response = _FakeCompletionResponse(
        content=None,
        tool_calls=[
            _FakeToolCall(
                name="subscription_agent",
                arguments=json.dumps({"user_request": "Quiero cancelar mi suscripción"}),
            )
        ],
        prompt_tokens=10,
        completion_tokens=3,
    )
    router = _build_router(
        monkeypatch,
        responses=[initial_response],
        fail_on_call_number=2,
    )

    subscription_result = AgentResult(
        response="Puedo cancelar tu suscripción cuando me lo confirmes explícitamente.",
        agent_used="subscription",
        metadata={"tool": "cancel_subscription", "result": {"success": True}},
        response_type="deterministic",
        risk_level="high",
    )
    monkeypatch.setattr(
        router,
        "_get_sub_agent",
        lambda agent_name: _StaticSubAgent(subscription_result) if agent_name == "subscription" else None,
    )

    result = await router.process(
        message="Quiero cancelar mi suscripción",
        phone="5491111111111",
        tenant_id="tenant-1",
        history=[],
    )

    assert result.response == "Puedo cancelar tu suscripción cuando me lo confirmes explícitamente."
    assert result.metadata is not None
    assert result.metadata["response_mode"] == "passthrough"
    assert result.metadata["finalizer_attempted"] is True
    assert result.metadata["finalizer_fallback_used"] is True
