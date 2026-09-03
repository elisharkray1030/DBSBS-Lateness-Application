FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/lateness_history.db \
    NAMELIST_PATH=/data/namelist.csv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py parser.py storage.py records.py ./
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data

EXPOSE 8000

# Explicit one-time database preparation runs before serving: importing the
# application performs no database I/O, and init-db is a safe no-op against
# an existing database.
CMD ["sh", "-c", "flask --app app init-db && gunicorn --bind 0.0.0.0:8000 app:app"]