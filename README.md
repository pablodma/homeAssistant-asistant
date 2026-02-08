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
- **LLM**: OpenAI (gpt-4.1-mini)
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
│ Finance Calendar│
│ Reminder Shop   │
│ Vehicle         │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│   homeai-api    │
│   (Backend)     │
└─────────────────┘
```

## Agentes

| Agente | Descripción |
|--------|-------------|
| Router | Orquestador principal, decide qué sub-agente usar |
| Finance | Gestión de gastos y presupuestos |
| Calendar | Eventos y sincronización con Google Calendar |
| Reminder | Recordatorios y alertas |
| Shopping | Listas de compras |
| Vehicle | Gestión de vehículos y mantenimiento |

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
