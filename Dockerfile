FROM python:3.11-slim

LABEL maintainer="ATC Radar Team"
LABEL description="Radar CAT62 ASTERIX Parser (synthetic/training use -- see README SCOPE)"

WORKDIR /app

# Install dependencies from the pinned requirements file for reproducibility.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (client includes the vendored Leaflet for offline use).
COPY parser_server.py .
COPY client/ client/

# Non-root user.
RUN useradd -m -u 1000 radar && chown -R radar:radar /app
USER radar

# HTTP UI/API, WebSocket, and the radar UDP input.
EXPOSE 7878 8765 31002/udp

# Health check hits the real liveness endpoint (returns 503 when degraded).
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7878/api/health').status==200 else 1)" || exit 1

CMD ["python", "parser_server.py", "--udp", "0.0.0.0:31002"]
