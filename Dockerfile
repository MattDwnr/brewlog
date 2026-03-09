ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --break-system-packages flask gunicorn

COPY rootfs/app /app

CMD ["gunicorn", "--bind", "0.0.0.0:8099", "--workers", "1", "--threads", "4", "--timeout", "60", "app:app"]
