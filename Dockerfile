FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy application code
COPY src/ src/

# Copy prompt configuration files
COPY docs/prompts/ docs/prompts/

# Expose port (Railway sets PORT env var)
EXPOSE ${PORT:-8000}

# Run the application using PORT env var
CMD uvicorn src.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
