# ADR-002: Ruteo LLM-Only para Router Agent

## Estado
**Aceptado** - 2026-02-09

## Contexto

El RouterAgent de HomeAI Assistant es responsable de decidir qué sub-agente (Finance, Calendar, Reminder, Shopping, Vehicle) debe manejar cada mensaje del usuario.

### Implementación anterior

El router tenía **dos capas de ruteo**:

1. **Keyword detection** (primera capa):
   ```python
   AGENT_KEYWORDS = {
       "finance": ["gast", "pagu", "plata", "dinero", ...],
       "calendar": ["reunión", "turno", "cita", "evento", ...],
       ...
   }
   ```
   - Rápido (~0ms de latencia)
   - Sin costo de tokens
   - Limitado a patrones exactos

2. **LLM fallback** (segunda capa):
   - Solo se activaba si keywords no matcheaban
   - Usaba OpenAI con tool calling
   - Más flexible pero más lento

### Problema

El keyword detection tenía limitaciones importantes:

1. **Falsos positivos**: "supermercado" matcheaba tanto `finance` como `shopping`
2. **Casos ambiguos mal manejados**: "Compré leche" (¿gasto o lista de compras?)
3. **Inconsistencia con arquitectura prompt-first**: La lógica de decisión estaba en código Python, no en el prompt
4. **Mantenimiento dual**: Había que mantener keywords Y el prompt del LLM

## Decisión

**Eliminar keyword detection y usar exclusivamente LLM para ruteo.**

### Implementación

```python
async def process(self, message, phone, tenant_id, history, **kwargs) -> AgentResult:
    """Process a message by routing to the appropriate sub-agent."""
    logger.info("Router processing message", phone=phone, message=message[:50])
    
    # Ruteo 100% via LLM - sin keyword detection
    return await self._process_with_llm(
        message=message,
        phone=phone,
        tenant_id=tenant_id,
        history=history,
    )
```

El prompt del router (`docs/prompts/router-agent.md`) contiene todas las reglas de ruteo:
- Diferenciaciones clave (gasto vs lista de compras, evento vs recordatorio)
- Cuándo NO usar herramientas (saludos, mensajes ambiguos)
- Ejemplos de cada caso

## Consecuencias

### Positivas

- **Consistencia prompt-first**: Toda la lógica de decisión vive en el prompt
- **Flexibilidad**: El LLM maneja casos ambiguos que keywords no podían
- **Mantenimiento simplificado**: Un solo lugar para cambiar reglas de ruteo
- **Mejor manejo de contexto**: El LLM considera el historial de conversación

### Negativas

- **Costo de tokens**: Cada mensaje ahora pasa por LLM (~500-1000 tokens por request)
- **Latencia adicional**: +200-500ms por la llamada al LLM
- **Dependencia de OpenAI**: Si OpenAI falla, el router falla

### Mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Costo alto | Monitorear tokens/mensaje, optimizar prompt si necesario |
| Latencia | Aceptable para UX de WhatsApp (objetivo < 3s total) |
| Disponibilidad OpenAI | Circuit breaker + mensaje de error amigable |

## Alternativas consideradas

### A. Mantener keyword detection como primera capa
- **Rechazada**: Inconsistente con arquitectura prompt-first

### B. Usar modelo local para ruteo (Llama, Mistral)
- **Rechazada**: Complejidad de infraestructura no justificada para MVP

### C. Clasificador ML entrenado específicamente
- **Rechazada**: Requiere datos de entrenamiento que aún no tenemos

## Referencias

- Arquitectura de agentes: `.cursor/rules/agents-architecture.mdc`
- Prompt del router: `docs/prompts/router-agent.md`
- ADR-001: Multi-tenancy Strategy
