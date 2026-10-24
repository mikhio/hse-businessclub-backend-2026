FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --system --uid 10001 runner
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
USER runner
CMD ["uvicorn", "app.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
