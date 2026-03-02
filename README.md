# HomeAI Assistant (Bot Service)

Servicio de bot de WhatsApp para HomeAI, construido con FastAPI y LangChain.

## Proyecto HomeAI

Sistema de asistente virtual del hogar multi-tenant que gestiona presupuestos, agenda, recordatorios y listas de compras via WhatsApp.

### Repositorios relacionados
| Local | GitHub | Deploy |
|-------|--------|--------|
| homeai-api | [homeAssistant-backend](https://github.com/pablodma/homeAssistant-backend) | Railway |
| homeai-web | [homeAssistant-frontend](https://github.com/pablodma/homeAssistant-frontend) | Vercel |
| homeai-assis | [homeAssistant-asistant](https://github.com/pablodma/homeAssistant-asistant) ← ESTE | Railway |

## Stack

- **Framework**: FastAPI (Python 3.11+)
- **LLM**: OpenAI (gpt-4.1-mini + gpt-4.1-nano)
- **Agent Framework**: LangChain
- **Database**: PostgreSQL (asyncpg)
- **WhatsApp**: Meta Business Cloud API
- **Deployment**: Railway

## Arquitectura

```
Usuario (WhatsApp)
       │
       ▼
┌─────────────────┐
│  Meta Cloud API │
└────────┬────────┘
         │ Webhook
         ▼
┌─────────────────┐
│  homeai-assis   │ ◄── ESTE SERVICIO
│  (Bot Service)  │
├─────────────────┤
│  Router Agent   │
│       │         │
│  ┌────┴────┐    │
│  ▼    ▼    ▼    │
│ Finance Agenda  │
│ Shopping Vehicle│
│ Subscription    │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│   homeai-api    │
│   (Backend)     │
└─────────────────┘
```

### Salida final híbrida del orquestador

El RouterAgent usa una política híbrida para responder al usuario:
- **Passthrough** por defecto para respuestas simples/determinísticas.
- **Finalización por orquestador** cuando hay múltiples sub-agentes o mayor riesgo.
- **Fallback automático a passthrough** si la finalización falla.

## Agentes

| Agente | Descripción | Prompt |
|--------|-------------|--------|
| Router | Orquestador principal, decide qué sub-agente usar | `docs/prompts/router-agent.md` |
| Router Finalizer | Redacción final unificada cuando aplica política híbrida | `docs/prompts/router-finalizer-agent.md` |
| Finance | Gestión de gastos y presupuestos | `docs/prompts/finance-agent.md` |
| Agenda | Eventos, recordatorios y sincronización con Google Calendar | `docs/prompts/calendar-agent.md` |
| Shopping | Listas de compras | `docs/prompts/shopping-agent.md` |
| Vehicle | Gestión de vehículos y mantenimiento | `docs/prompts/vehicle-agent.md` |
| Subscription | Gestión de plan, uso, upgrade/cancelación e invitaciones | `docs/prompts/subscription-agent.md` |

## Prompts de Agentes

Los prompts de los agentes están en `docs/prompts/` como archivos markdown.

### Arquitectura Prompt-First

La lógica de decisión de los agentes vive en los prompts, NO en código Python:

```
docs/prompts/
├── router-agent.md     # Cómo decide qué sub-agente usar
├── router-finalizer-agent.md # Cómo sintetiza la respuesta final
├── finance-agent.md    # Reglas para registrar gastos, presupuestos
├── calendar-agent.md   # Manejo de eventos y agenda
├── shopping-agent.md   # Listas de compras
├── vehicle-agent.md    # Gestión de vehículos
├── subscription-agent.md # Gestión de suscripción y miembros
└── qa-agent.md         # Control de calidad
```

### Modificar comportamiento de un agente

1. Editar `docs/prompts/{agent}-agent.md`
2. Commit + push
3. Railway redeploya automáticamente (~30 segundos)

## Desarrollo Local

### Requisitos
- Python 3.11+
- PostgreSQL (o usar Railway)

### Setup

```bash
# Crear virtual environment
python -m venv .venv
.venv\Scripts\activate     # Windows
source .venv/bin/activate  # Linux/Mac

# Instalar dependencias
pip install -e ".[dev]"

# Copiar configuración
cp .env.example .env
# Editar .env con tus valores

# Ejecutar
uvicorn src.app.main:app --reload
```

### Testing local de webhook

Para probar el webhook localmente, usa ngrok:

```bash
ngrok http 8000
```

Luego configura la URL en Meta Business Portal.

## Variables de Entorno

| Variable | Descripción | Requerido |
|----------|-------------|-----------|
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de WhatsApp Business | ✅ |
| `WHATSAPP_VERIFY_TOKEN` | Token para verificar webhook | ✅ |
| `WHATSAPP_ACCESS_TOKEN` | Token de acceso de Meta | ✅ |
| `OPENAI_API_KEY` | API key de OpenAI | ✅ |
| `OPENAI_MODEL` | Modelo para sub-agentes | ❌ |
| `OPENAI_ROUTER_MODEL` | Modelo para ruteo del orquestador | ❌ |
| `ORCHESTRATOR_FINALIZER_ENABLED` | Habilita finalización híbrida del router | ❌ |
| `ORCHESTRATOR_FINALIZE_ON_MULTI_AGENT_ONLY` | Limita finalizer a casos multi-agente | ❌ |
| `ORCHESTRATOR_FINALIZER_MODEL` | Modelo para la pasada de finalización | ❌ |
| `DATABASE_URL` | PostgreSQL connection string | ✅ |
| `BACKEND_API_URL` | URL del backend API | ✅ |
| `BACKEND_API_KEY` | API key para el backend | ✅ |
| `APP_ENV` | development/production | ❌ |

## Deploy en Railway

1. Crear nuevo servicio en Railway
2. Conectar repositorio GitHub
3. Agregar variables de entorno desde `.env.example`
4. Conectar a PostgreSQL existente
5. Configurar webhook en Meta Business Portal

## Webhook de WhatsApp

- **Verificación**: `GET /webhook?hub.mode=subscribe&hub.verify_token=TOKEN&hub.challenge=CHALLENGE`
- **Mensajes**: `POST /webhook` (recibe eventos de WhatsApp)

## Licencia

Privado - Todos los derechos reservados.
