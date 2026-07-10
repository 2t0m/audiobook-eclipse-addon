# Use Python 3.13 slim image
FROM python:3.13-slim

# Build argument to invalidate cache
ARG CACHEBUST=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create cache directory
RUN mkdir -p /app/cache

# Expose port
EXPOSE 5001

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV APP_LOG_LEVEL=INFO
ENV GUNICORN_LOG_LEVEL=WARNING

# Run with Gunicorn with dynamic log level
CMD GUNICORN_LEVEL=$(echo "${GUNICORN_LOG_LEVEL:-WARNING}" | tr '[:upper:]' '[:lower:]') && \
    if [ "$GUNICORN_LEVEL" = "info" ] || [ "$GUNICORN_LEVEL" = "debug" ]; then \
        ACCESS_LOG="-"; \
    else \
        ACCESS_LOG="/dev/null"; \
    fi && \
    exec gunicorn \
    --bind 0.0.0.0:5001 \
    --workers 1 \
    --threads 4 \
    --timeout 300 \
    --log-level "$GUNICORN_LEVEL" \
    --access-logfile "$ACCESS_LOG" \
    --error-logfile - \
    --capture-output \
    app:app
