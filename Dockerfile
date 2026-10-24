FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/journal.db

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY samples ./samples
RUN pip install --no-cache-dir .

# Run as an unprivileged user; the journal lives on a mounted volume.
RUN useradd --create-home --uid 10001 bot && mkdir /data && chown bot /data
USER bot
VOLUME /data

CMD ["python", "-m", "journalbot"]
