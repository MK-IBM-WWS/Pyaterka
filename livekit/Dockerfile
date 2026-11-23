FROM livekit/livekit-server:v1.13.9 AS upstream
FROM python:3.12-slim-trixie
COPY --from=upstream /livekit-server /usr/local/bin/livekit-server
COPY dns_supervisor.py /app/dns_supervisor.py
ENTRYPOINT ["python", "-u", "/app/dns_supervisor.py"]
