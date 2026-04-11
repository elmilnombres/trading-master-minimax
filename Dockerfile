FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies (httpx is already in requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source — all sub-packages
COPY apps/ apps/
COPY core/ core/
COPY exchange/ exchange/
COPY schemas/ schemas/
COPY configs/ configs/

# Copy deploy utilities (healthcheck script, etc.)
COPY deploy/ deploy/

ENV PYTHONPATH=/app
