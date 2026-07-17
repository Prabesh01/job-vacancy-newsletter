FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY crontab /etc/cron.d/vacnewsletter-cron
RUN chmod 0644 /etc/cron.d/vacnewsletter-cron && crontab /etc/cron.d/vacnewsletter-cron

RUN mkdir -p /app/data && chmod +x entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["gunicorn", "--bind", "0.0.0.0:9086", "app:app"]
