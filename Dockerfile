FROM python:3.11-slim

LABEL maintainer="ATC Radar Team"
LABEL description="Professional Radar CAT62 ASTERIX Parser"

# Set working directory
WORKDIR /app

# Copy application files
COPY parser_server.py .
COPY client/ client/

# Install dependencies
RUN pip install --no-cache-dir websockets

# Create non-root user
RUN useradd -m -u 1000 radar && chown -R radar:radar /app
USER radar

# Expose ports
EXPOSE 7878 8765 31002/udp

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7878/api/health')" || exit 1

# Default command
CMD ["python", "parser_server.py", "--udp", "0.0.0.0:31002"]
