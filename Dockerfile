FROM python:3.12-alpine3.21

RUN apk update && \
    apk add --no-cache skopeo ca-certificates && \
    rm -rf /var/cache/apk/*

WORKDIR /app

COPY requirements.txt .

RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del .build-deps

COPY . .

RUN adduser -D appuser
RUN chown -R appuser:appuser /app
USER appuser

ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV GUNICORN_CMD_ARGS="--worker-class=gthread --workers=4 --threads=1 --timeout=120 --bind=0.0.0.0:8008"

EXPOSE 8008

CMD ["gunicorn", "app:app"]