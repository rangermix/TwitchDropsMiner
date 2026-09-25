FROM python:3-alpine

# Build arguments for metadata
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION

# Labels following OCI Image Format Specification
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.authors="rangermix" \
      org.opencontainers.image.url="https://github.com/rangermix/TwitchDropsMiner" \
      org.opencontainers.image.documentation="https://github.com/rangermix/TwitchDropsMiner/blob/main/README.md" \
      org.opencontainers.image.source="https://github.com/rangermix/TwitchDropsMiner" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.vendor="rangermix" \
      org.opencontainers.image.title="Twitch Drops Miner" \
      org.opencontainers.image.description="Automated Twitch drops mining application with web-based interface"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Chromium, for minting client-integrity tokens through Streamlink.
# See src/auth/integrity.py and docs/streamlink-integrity.md.
# xvfb-run: Chromium has to run headful for Twitch to accept its tokens.
RUN apk add --no-cache chromium nss freetype harfbuzz ttf-freefont xvfb-run
# Alpine's launcher appends CHROMIUM_USER_FLAGS; Chromium won't start as root
# without --no-sandbox.
ENV TDM_CHROMIUM_PATH=/usr/bin/chromium-browser \
    CHROMIUM_USER_FLAGS=--no-sandbox

# Set working directory
WORKDIR /app

# Copy project metadata and install dependencies
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir .

# Copy application code
COPY main.py ./
COPY src/ ./src/
COPY lang/ ./lang/
COPY icons/ ./icons/
COPY web/ ./web/

# Create data directory for persistent storage
RUN mkdir -p /app/data && chmod 777 /app/data
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Expose web port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Run the application (web GUI is now default)
CMD ["python", "main.py"]
