FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY schema.sql seed.py ./
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app && \
    mkdir -p /data && chown app:app /data
ENV PATH="/app/.venv/bin:$PATH" DATABASE_PATH=/data/facility.db
USER app
EXPOSE 8000
# Embedded APScheduler must never be run with multiple workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
