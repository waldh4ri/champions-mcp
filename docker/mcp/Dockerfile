FROM python:3.12-slim

WORKDIR /app

# Install build tools needed by some Python deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
  && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY data/ ./data/

# Install the package (editable so data/ path resolution works as expected)
RUN pip install --no-cache-dir -e .

COPY docker/mcp/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
