FROM selenium/standalone-chrome:4.49.0-20260909

# Docker initializes a new named volume with these permissions. The browser
# runs as seluser; its profile must not be readable by other container users.
USER root
RUN mkdir -p /home/seluser/tdm-profile \
    && chown seluser:seluser /home/seluser/tdm-profile \
    && chmod 700 /home/seluser/tdm-profile
USER seluser
