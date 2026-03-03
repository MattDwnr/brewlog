ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --break-system-packages flask

COPY rootfs/app /app

CMD ["python3", "/app/app.py"]
