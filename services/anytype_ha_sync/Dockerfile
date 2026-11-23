FROM ghcr.io/anyproto/anytype-cli:latest AS anytype_bin

FROM python:3.12-alpine

WORKDIR /app
RUN apk add --no-cache ca-certificates

COPY --from=anytype_bin /usr/local/bin/anytype /usr/local/bin/anytype

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY network.yaml /app/network.yaml
COPY sync.py /app/sync.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

VOLUME ["/root/.anytype", "/root/.config/anytype"]

ENTRYPOINT ["/app/entrypoint.sh"]
