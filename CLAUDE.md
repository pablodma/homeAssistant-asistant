# homeai-assis — Bot Agent

Bot de WhatsApp para HomeAI. Recibe webhooks de Meta, resuelve phone→tenant, delega a agentes LLM, envía respuestas, loguea interacciones y ejecuta QA asíncrono.

## Ownership

Este agente es responsable de TODO lo que vive en `homeai-assis/`. No modifica homeai-api ni frontends.

## Stack

- Python 3.11, FastAPI, asyncpg
- OpenAI (gpt-4.1-mini para sub-agentes, gpt-4.1-nano para router)
- Anthropic Claude (claude-sonnet-4-20250514 para QA, claude-opus-4-6 para PromptImprover)
- structlog para logging estructurado
- Package manager: `pip install -e .`

## Estructura

```
src/app/
├── main.py               # FastAPI app, lifespan
├── config/settings.py    # Pydantic BaseSettings
├── whatsapp/
│   ├── webhook.py        # POST/GET /webhook — entry point
│   ├── client.py         # WhatsAppClient (enviar mensajes)
│   └── types.py          # Pydantic models para payloads Meta
├── agents/
│   ├── base.py           # BaseAgent abstracto
│   ├── router.py         # RouterAgent — orquestador LLM-only
│   ├── finance.py        # FinanceAgent
│   ├── calendar.py       # AgendaAgent (calendario + recordatorios)
│   ├── shopping.py       # ShoppingAgent (DB directo)
│   ├── vehicle.py        # VehicleAgent (DB directo)
│   └── qa.py             # QAAgent (Anthropic, async)
├── services/
│   ├── phone_resolver.py # phone → tenant_id (cache + backend API)
│   ├── conversation.py   # Memoria de chat (24h, 10 msgs)
│   ├── prompt_loader.py  # Carga prompts desde docs/prompts/
│   ├── interaction_log.py
│   ├── quality_logger.py # hard_error / soft_error
│   ├── input_guard.py    # Sanitización de entrada
│   ├── output_guard.py   # Validación de salida
│   ├── quick_actions.py  # Acciones rápidas (botones WhatsApp)
│   ├── backend_client.py # httpx client para homeai-api
│   ├── qa_reviewer.py    # PromptImprover (Anthropic → GitHub)
│   └── github.py         # Edición de prompts vía GitHub API
└── repositories/         # asyncpg queries directas (shopping, vehicle)
docs/prompts/             # *** PROMPTS DE AGENTES (fuente de verdad) ***
tests/
```

## Principio FUNDAMENTAL: PROMPT-FIRST

**La lógica de decisión SIEMPRE va en el prompt, NO en código Python.**

- El código ejecuta herramientas (HTTP calls, DB queries, format)
- El prompt decide CUÁNDO y CÓMO usar cada herramienta
- Cambio de comportamiento de agente = cambio en `docs/prompts/` → commit → Railway redeploya

### Checklist antes de escribir código en agents/:
- [ ] ¿Puedo lograr esto solo con cambios al prompt?
- [ ] ¿Estoy poniendo lógica de decisión en Python?
- Si "sí" a cualquiera → revisar el approach

## Flujo de un Mensaje

```
POST /webhook
  → verificar signature HMAC (requerido en prod)
  → rate limit por phone (20/min in-memory)
  → parsear payload Meta
  → background_task:
      PhoneResolver → tenant_id
      ConversationService → history
      sanitize_message (input_guard)
      RouterAgent → sub-agente correcto
      sub-agente.process() → AgentResult
      check_response (output_guard)
      WhatsAppClient.send()
      InteractionLogger.log()
      QAAgent.analyze() (async, no bloquea)
```

## Prompts de Agentes

```
docs/prompts/
├── router-agent.md           # Orquestador — ruteo LLM-only
├── router-finalizer-agent.md # Finalización híbrida
├── finance-agent.md
├── calendar-agent.md         # Agenda (eventos + recordatorios)
├── shopping-agent.md
├── vehicle-agent.md
├── subscription-agent.md
└── qa-agent.md
```

Para cambiar comportamiento de un agente: editar el `.md` → commit → push → Railway redeploya (~30s).

## Comandos

```bash
# Setup
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

# Dev server
uvicorn src.app.main:app --reload --port 8001

# Tests
pytest tests/ -v

# Test webhook local (requiere ngrok)
ngrok http 8001
```

## Comunicación con Backend (homeai-api)

El bot usa Bearer token de servicio (`BACKEND_API_KEY`), nunca JWT de usuario.

```python
# Finance
POST {BACKEND_API_URL}/api/v1/tenants/{tenant_id}/agent/expense
# Agenda
POST {BACKEND_API_URL}/api/v1/tenants/{tenant_id}/agent/calendar/events
# Phone lookup
GET  {BACKEND_API_URL}/api/v1/phone/lookup?phone={e164}
```

Shopping y Vehicle escriben **directo a PostgreSQL** (mismo `DATABASE_URL` que homeai-api).

## Variables de Entorno Requeridas

`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET` (prod), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `BACKEND_API_URL`, `BACKEND_API_KEY`

## Quality System

- **QAAgent** (claude-sonnet): analiza cada interacción async, detecta misinterpretation/hallucination/unsupported/incomplete
- **PromptImprover** (claude-opus-4-6): cuando acumulan ≥2 issues → propone mejoras al prompt → PR en GitHub
- `quality_logger`: registra `hard_error` (técnicos) y `soft_error` (QA) en tabla `quality_issues`

## Guardrails

- Nunca loguear `WHATSAPP_ACCESS_TOKEN` ni `OPENAI_API_KEY`
- Toda query a DB filtra por `tenant_id` — sin excepciones
- Respuestas máx ~4000 chars (límite WhatsApp)
- `WHATSAPP_APP_SECRET` debe estar configurado en producción (el app falla con 500 si no)
- Rate limiter in-memory se resetea en cada restart — aceptado como trade-off
- Si el bot modifica `docs/prompts/` → siempre crear branch + PR, nunca push directo a `main`
