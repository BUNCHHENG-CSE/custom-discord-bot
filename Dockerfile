FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY config.py database.py gateway.py interactions.py lavalink.py main.py ./
COPY prefix_commands.py rest.py state.py schema.sql ./
COPY modules/ ./modules/

RUN useradd --create-home --uid 10001 bot \
    && chown -R bot:bot /app

USER bot

CMD ["python", "main.py"]
