FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

# Same layout as the repo: code in /app/backend, the repo's data/ folder mounted at /app/data
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

ENV PATH="/app/backend/.venv/bin:$PATH" \
    DATA_DIR=/app/data/runtime
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
