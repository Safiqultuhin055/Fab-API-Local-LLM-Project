# Multi-stage build for the AI Gateway.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools for wheels + Microsoft ODBC Driver 18 (SQL Server).
# The driver lets aioodbc/pyodbc reach the SQL Server. The app is SQL-Server-only
# (no SQLite fallback), so this driver is required at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl gnupg apt-transport-https unixodbc-dev ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
       -o /etc/apt/sources.list.d/mssql-release.list \
    && sed -i 's#deb #deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] #' \
       /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
# RAG knowledge base is read AND appended to at runtime (POST /v1/rag/*).
# Baked in as a default; bind-mount ./rag in compose to persist writes.
COPY rag ./rag

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
