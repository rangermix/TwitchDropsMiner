# Extract only noVNC's browser library, without installing its websockify server.
FROM python:3.14-slim-trixie AS novnc-assets
RUN apt-get update \
    && apt-get download novnc \
    && dpkg-deb --extract novnc_*.deb /novnc

FROM python:3.14-slim-trixie

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

# Set working directory
WORKDIR /app

# Login and renewal use private browsers; only the dashboard port is exposed.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends chromium xvfb openbox x11vnc xdotool tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=novnc-assets /novnc/usr/share/novnc/core/ /usr/share/novnc/core/
COPY --from=novnc-assets /novnc/usr/share/novnc/vendor/ /usr/share/novnc/vendor/
COPY --from=novnc-assets /novnc/usr/share/doc/novnc/copyright /usr/share/novnc/copyright
RUN groupadd --gid 10001 tdm-browser \
    && useradd --uid 10001 --gid tdm-browser --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin tdm-browser

# Copy project metadata and install dependencies
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir . \
    && pip uninstall --yes pip

# Copy application code
COPY main.py ./
COPY src/ ./src/
COPY lang/ ./lang/
COPY icons/ ./icons/
COPY web/ ./web/

# Create data directory for persistent storage
RUN mkdir -p /app/data /app/logs && chmod 700 /app/data /app/logs

# Expose web port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Run the application (web GUI is now default)
CMD ["python", "main.py"]
