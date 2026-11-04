# Extract only noVNC's browser library, without installing its websockify server.
FROM alpine:latest AS novnc-assets
RUN apk add --no-cache novnc

# Twitch only accepts integrity tokens from branded Google Chrome builds.
# Alpine Chromium is rejected even when its client hints are overridden, so the
# image is Debian-based and installs Chrome instead of the distro Chromium.
FROM python:3.12-slim-bookworm

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
# Chrome comes from Google's signed apt repository so package signatures are checked.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb xauth openbox x11vnc tzdata ca-certificates curl fonts-liberation gnupg \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
        https://dl.google.com/linux/linux_signing_key.pub -o /tmp/google-chrome.asc \
    && test "$(gpg --show-keys --with-colons /tmp/google-chrome.asc \
        | awk -F: '/^fpr:/ {print $10; exit}')" = "EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796" \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /tmp/google-chrome.asc \
    && rm -f /tmp/google-chrome.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge --auto-remove -y curl gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && google-chrome --version
COPY --from=novnc-assets /usr/share/novnc/ /usr/share/novnc/
RUN groupadd -g 10001 tdm-browser \
    && useradd -u 10001 -g tdm-browser -M -d /nonexistent -s /usr/sbin/nologin tdm-browser \
    && ln -s /usr/bin/google-chrome /usr/local/bin/chromium \
    && ln -s /usr/bin/google-chrome /usr/local/bin/chromium-browser

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
