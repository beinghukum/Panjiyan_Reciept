# Use Microsoft Playwright base image — has Chromium + all deps pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot.py scraper.py ./

# Render sets PORT env var — not needed for bots, but good practice
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
