# champions-mcp server image: installs the Python package and its bundled
# static data, then hands off to entrypoint.sh to build/verify the local
# SQLite mirror and start the MCP server.
FROM python:3.12-slim

WORKDIR /app

# Project metadata first, separately from source: pyproject.toml/README.md
# change far less often than src/, so this layer stays cached across most
# code edits and only invalidates when dependencies actually change.
COPY pyproject.toml README.md ./
COPY src/ ./src/
# Bundled static data (e.g. formats.json, any prebuilt champions.sqlite) that
# entrypoint.sh seeds into the persistent /data volume on first start.
COPY data/ ./data/
# MCP prompt/skill guide content (see docker/chat/main.py's SKILL_PROMPT_NAME)
# served to clients via prompts/get.
COPY skills/ ./skills/

# Editable install (-e): the package is only ever run from this image, so
# there's no benefit to a separate build/wheel step — this just registers
# src/ on the path and installs the pyproject.toml dependencies.
RUN pip install --no-cache-dir -e .

# Startup script: seeds bundled data into the volume, builds the local
# champions.sqlite mirror on first run (or when forced), then execs the
# server. Copied after the pip install so entrypoint edits don't bust the
# dependency-install layer cache.
COPY docker/mcp/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# MCP streamable-HTTP port (see FASTMCP_PORT in docker-compose.yml).
EXPOSE 8000

# Delegate startup to entrypoint.sh, which builds/refreshes the data mirror
# before execing the actual `champions-mcp` server process.
ENTRYPOINT ["/entrypoint.sh"]
