FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *dependencies])' \
    && python -m playwright install --with-deps chromium

COPY README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

ENV SENPILOT_RUNTIME_DIR=/app/runtime \
    SENPILOT_HEADLESS=true \
    PYTHONUNBUFFERED=1

CMD ["senpilot-agent", "run"]
