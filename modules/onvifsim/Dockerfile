FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies only
RUN uv sync --frozen --no-dev

# Copy application source
COPY *.py config.yaml ./

# Bake images into the container (drop Kaggle dataset into images/ before building).
# Override at runtime with -e IMAGES_DIR=/your/path or a volume mount.
COPY images/ /images/

# Non-root user
RUN adduser --disabled-password --gecos "" appuser
USER appuser

EXPOSE 8080

ENV IMAGES_DIR=/images

ENTRYPOINT ["uv", "run", "python", "app.py"]
