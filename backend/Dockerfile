FROM python:3.11-slim

# PYTHONUNBUFFERED=1 — so print() statements from main.py lifespan (and any other
# synchronous-write logging path) flush immediately to docker logs instead of
# sitting in a block buffer. Before this flag, startup messages like
# "Auto-started agent X" were silently invisible in docker logs.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies + Node.js 20 (needed for the Dukascopy-direct
# backtest fetcher — see app/services/backtest/data_fetcher.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (separate step for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/ml_models /tmp/flowrex-backtest

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/api/health'); assert r.status_code == 200"

# Start command: run migrations then start server (single worker for 2GB RAM)
CMD ["sh", "-c", "python -m alembic upgrade head || echo 'WARN: alembic migration failed'; uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1"]
