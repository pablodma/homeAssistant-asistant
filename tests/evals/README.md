# LLM Evaluation Tests

These tests use DeepEval/GEval to evaluate actual LLM behavior.
They make real API calls and are NOT run in CI by default.

## Running evals

```bash
uv run pytest tests/evals/ -v --no-header
```

## Adding new evals

Use `deepeval` for LLM quality metrics. Never use text matching.
