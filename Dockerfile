# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright
RUN pip install playwright playwright-stealth aiohttp
RUN playwright install chromium
RUN playwright install-deps

# Copy application
COPY app.py scraper.py ./

# Create data directory
RUN mkdir -p /app/data

# Run scraper
CMD ["python", "app.py", "--max-retries", "3"]