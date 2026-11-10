FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd -g 10001 vibe && \
    useradd -u 10001 -g vibe -m -s /bin/bash vibe

# Install python dependencies
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy application source
COPY . /app

# Ensure proper permissions for non-root user
RUN mkdir -p /app/uploads/analytics && \
    chown -R vibe:vibe /app

USER vibe

CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]

