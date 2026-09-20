# Playwright official image: Python + Chromium (headless) + matching drivers.
# Multi-arch (amd64/arm64) -> works also on ARM mini PCs.
# The image ships a non-root user "pwuser".
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_STATE_PATH=/data/storage_state.json \
    DEBUG_DIR=/data/debug

WORKDIR /home/pwuser/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collect_coins.py .

# /data holds the saved session (cookies) and debug screenshots;
# it is mounted as a Docker volume.
RUN mkdir -p /data && chown -R pwuser:pwuser /data

USER pwuser

CMD ["python", "collect_coins.py"]
