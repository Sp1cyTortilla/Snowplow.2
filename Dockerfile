# Snowfall tracker: FastAPI + collector + plow tracker in one container.
FROM python:3.13-slim

# uv installs the exact package versions pinned in uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    SNOW_DATA_DIR=/app/data

WORKDIR /app

# Dependencies first so code changes don't reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY snowtracker ./snowtracker

# Database, zip dataset, cached boundaries and plow sources live here (mount a volume)
RUN mkdir -p /app/data && chmod 777 /app/data
VOLUME /app/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000"]
