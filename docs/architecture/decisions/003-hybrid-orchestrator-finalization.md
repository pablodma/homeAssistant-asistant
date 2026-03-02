# ADR-003: Finalización Híbrida del Orquestador

## Estado
**Aceptado** - 2026-03-01

## Contexto

El `RouterAgent` ya centraliza el ruteo de intenciones (ADR-002), pero la respuesta final al usuario venía mayormente redactada por sub-agentes.

Esto generaba un tradeoff:
- Si el router siempre reescribe: mejor consistencia de voz, pero mayor latencia/costo.
- Si siempre hace passthrough: menor latencia/costo, pero más riesgo de inconsistencias en respuestas complejas.

## Decisión

Adoptar un esquema **híbrido** de salida final:

1. **Passthrough por defecto** para respuestas simples y determinísticas.
2. **Finalización por orquestador** cuando aplica alguno:
   - múltiples sub-agentes en el mismo turno
   - acciones sensibles/destructivas
   - errores parciales
   - señales de fuga de identidad de sub-agente
3. **Fallback a passthrough** si la finalización falla.

La finalización del orquestador se gobierna por prompt dedicado:
- `docs/prompts/router-finalizer-agent.md`

## Implementación

- `src/app/agents/router.py`
  - `RouterAgent._should_finalize(...)`
  - `RouterAgent._finalize_response(...)`
  - metadata de observabilidad:
    - `response_mode`
    - `finalizer_attempted`
    - `finalizer_fallback_used`

- `src/app/agents/base.py`
  - `AgentResult` extiende pistas opcionales:
    - `response_type`
    - `risk_level`
    - `requires_orchestrator_final`

- `src/app/config/settings.py`
  - `orchestrator_finalizer_enabled`
  - `orchestrator_finalize_on_multi_agent_only`
  - `orchestrator_finalizer_model`

## Consecuencias

### Positivas
- Mejor consistencia de identidad (Aira) en casos complejos.
- Mantiene costo/latencia bajos en el camino común.
- Rollout controlado por feature flags.

### Negativas
- Mayor complejidad en el router.
- Posible distorsión de hechos si el finalizer no está bien restringido.

### Mitigaciones
- Prompt del finalizer con reglas de no alterar hechos.
- Fallback automático a passthrough en error.
- Observabilidad explícita del modo de respuesta.

## Referencias

- ADR-002: `docs/architecture/decisions/002-llm-only-routing.md`
- Prompt finalizer: `docs/prompts/router-finalizer-agent.md`
- Router: `src/app/agents/router.py`
