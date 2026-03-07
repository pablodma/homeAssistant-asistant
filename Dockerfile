FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies
RUN uv sync --no-dev --system --frozen

# Copy application code
COPY src/ src/

# Copy prompt configuration files
COPY docs/prompts/ docs/prompts/

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port (Railway sets PORT env var)
EXPOSE ${PORT:-8000}

# Run the application
CMD uvicorn src.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
