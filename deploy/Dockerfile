FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && python -m playwright install --with-deps chromium

ENV SENPILOT_RUNTIME_DIR=/app/runtime \
    SENPILOT_HEADLESS=true \
    PYTHONUNBUFFERED=1

CMD ["senpilot-agent", "run"]
