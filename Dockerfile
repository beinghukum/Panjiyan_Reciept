FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py scraper.py ./

ENV PYTHONUNBUFFERED=1
# Playwright needs this on some container environments
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

CMD ["python", "bot.py"]
