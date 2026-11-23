FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/data \
    PATH="/data/.local/bin:/usr/local/bin:/usr/bin:/bin"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    jq \
    procps \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY run.sh /app/run.sh
COPY agy_bridge_server.py /app/agy_bridge_server.py
RUN chmod +x /app/run.sh /app/agy_bridge_server.py

EXPOSE 11435
CMD ["/app/run.sh"]
