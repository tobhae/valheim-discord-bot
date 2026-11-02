FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY config.example.toml config.toml

RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot

CMD ["python", "-m", "bot"]
