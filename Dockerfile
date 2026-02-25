FROM python:3.11-slim

WORKDIR /rythmx

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY webui/ ./webui/
COPY scripts/ ./scripts/

# Create data directory (will be overridden by volume mount)
RUN mkdir -p /data/cc

# Secrets and data are provided at runtime via env vars and volume mounts.
# Never bake .env or db files into the image.
ENV PYTHONUNBUFFERED=1

EXPOSE 8009

CMD ["python", "-m", "app.main"]
