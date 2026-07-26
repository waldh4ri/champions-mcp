# champions-chat image: a light FastAPI bridge between the web UI, the
# llama-server chat backend, and champions-mcp's tools (see main.py).
FROM python:3.12-slim

WORKDIR /app

# No pyproject.toml here — this service is a single small script, so its
# few dependencies are installed directly rather than via a package install.
RUN pip install --no-cache-dir fastapi uvicorn httpx python-multipart

# App code and static assets copied last (after the dependency install) so
# editing main.py or the UI doesn't invalidate the pip-install layer cache.
COPY docker/chat/main.py ./
COPY docker/chat/static/ ./static/

# Web UI / chat API port (see WEBUI_PORT in docker-compose.yml).
EXPOSE 3000

# Serve the FastAPI app with uvicorn; 0.0.0.0 so the published port mapping
# can reach it, port fixed at 3000 to match the EXPOSE above.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
