FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/lateness_history.db \
    NAMELIST_PATH=/data/namelist.csv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py parser.py ./
COPY templates ./templates

RUN mkdir -p /data

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]