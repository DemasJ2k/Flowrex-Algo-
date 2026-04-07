FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (separate step for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/ml_models

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/api/health'); assert r.status_code == 200"

# Start command: run migrations then start server (single worker for 2GB RAM)
CMD ["sh", "-c", "python -m alembic upgrade head || echo 'WARN: alembic migration failed'; uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1"]
