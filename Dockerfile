# Stage 1: Build the React frontend
FROM node:20-alpine AS builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime — Flask serves the built React app
FROM python:3.11-slim
WORKDIR /rythmx

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/

# Copy the compiled React app into the webui/ folder Flask serves
COPY --from=builder /build/dist ./webui/

# Create data directory (will be overridden by volume mount)
RUN mkdir -p /data/cc

# Secrets and data are provided at runtime via env vars and volume mounts.
# Never bake .env or db files into the image.
ENV PYTHONUNBUFFERED=1

EXPOSE 8009

CMD ["python", "-m", "app.main"]
